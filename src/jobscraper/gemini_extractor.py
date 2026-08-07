"""Gemini-based JD skill extraction: turns each job's free-text description
into a ranked list of the skills/technologies it actually requires (most ->
least emphasized in the text), so ranking.py can match against the
candidate's real skill set instead of only scanning for a fixed keyword
list. Batches multiple JDs into a single request to keep API call volume
down — this only ever runs on jobs that already passed the experience and
location hard filters in pipeline.py.

Built for the Gemini API free tier, which is stingy on requests-per-minute
and requests-per-day (and Google doesn't publish one fixed guaranteed
number -- see config.yaml's gemini section). Two mechanisms keep a run from
blowing through those limits:
  - _RateLimiter throttles outbound calls to stay under requests_per_minute,
    sleeping as needed rather than firing a burst of batches instantly.
  - A per-day request count, persisted via Database.get/set_state so it
    survives across separate GitHub Actions runs, stops calling Gemini once
    requests_per_day is spent -- any jobs left over that day simply don't
    get Gemini-extracted keywords.
A 429/RESOURCE_EXHAUSTED response also gets a few exponential-backoff
retries (see _is_retryable) before the batch is given up on.

Never raises: any request/parse failure -- including exhausting retries --
is logged and the affected jobs simply keep an empty jd_keywords list,
which ranking.py treats as "fall back to the static substring match" (see
ranking._match_skills). A Gemini outage, missing API key, or spent rate
budget must never break the daily run.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

from google import genai
from google.genai import types

from jobscraper.models import Job

logger = logging.getLogger(__name__)

_PROMPT_HEADER = (
    "You are analyzing job descriptions to extract the skills, technologies, "
    "and requirements each one actually asks for, ordered from most to least "
    'emphasized/important in the text. For each job below (identified by its "id"), '
    "return 5-12 skills/technologies, most important first. Only list skills the "
    "text actually implies -- do not invent ones. Respond with ONLY a JSON array "
    "in this exact shape, no other text:\n"
    '[{"id": "<job id>", "skills": ["skill 1", "skill 2", ...]}]\n\n'
    "Jobs:\n"
)

# Keeps a single batch's prompt (and token cost) bounded regardless of how
# long an individual JD is.
_MAX_DESCRIPTION_CHARS = 4000

# Rate-limit backoff: a free-tier RPM window is 60s, so the first retry
# waits long enough for that window to plausibly have rolled over rather
# than immediately re-hitting the same 429.
_INITIAL_BACKOFF_SECONDS = 20.0
_BACKOFF_MULTIPLIER = 2.0

_DAILY_BUDGET_STATE_KEY = "gemini_daily_request_count"


class _RateLimiter:
    """Blocks (via time.sleep) before any call that would push the count of
    calls in the trailing 60s above `requests_per_minute`. A sliding
    window rather than a fixed per-minute bucket, so it can't be gamed by
    bunching requests right at a minute boundary."""

    def __init__(self, requests_per_minute: int) -> None:
        self._limit = max(1, requests_per_minute)
        self._timestamps: deque[float] = deque()

    def _drop_expired(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] >= 60:
            self._timestamps.popleft()

    def wait(self) -> None:
        now = time.monotonic()
        self._drop_expired(now)
        if len(self._timestamps) >= self._limit:
            sleep_for = 60 - (now - self._timestamps[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            self._drop_expired(now)
        self._timestamps.append(now)


def _is_retryable(exc: Exception) -> bool:
    """True for rate-limit (429) and transient server (503) responses --
    the two cases where backing off and retrying the same batch can
    plausibly succeed. Checks both the SDK's structured error code and the
    raw message, since not every raise path in google-genai guarantees a
    populated `.code`."""
    code = getattr(exc, "code", None)
    if code in (429, 503):
        return True
    text = str(exc).upper()
    return "RESOURCE_EXHAUSTED" in text or "429" in text or "UNAVAILABLE" in text


def _build_prompt(jobs: list[Job]) -> str:
    parts = [_PROMPT_HEADER]
    for job in jobs:
        description = (job.description or job.title)[:_MAX_DESCRIPTION_CHARS]
        parts.append(f"--- id: {job.job_hash} ---\nTitle: {job.title}\n{description}\n")
    return "\n".join(parts)


def _parse_response(text: str) -> dict[str, list[str]]:
    data = json.loads(text)
    result: dict[str, list[str]] = {}
    for entry in data:
        job_id = entry.get("id")
        skills = entry.get("skills")
        if job_id and isinstance(skills, list):
            result[job_id] = [str(s).strip() for s in skills if str(s).strip()]
    return result


def _extract_one_batch(
    client: genai.Client,
    model: str,
    jobs: list[Job],
    limiter: _RateLimiter,
    max_retries: int,
    budget: _DailyBudget,
) -> dict[str, list[str]]:
    prompt = _build_prompt(jobs)
    backoff = _INITIAL_BACKOFF_SECONDS
    for attempt in range(max_retries + 1):
        if budget.exhausted():
            logger.warning(
                "Gemini daily request budget reached mid-retry; giving up on "
                "this batch of %d job(s) for today", len(jobs),
            )
            return {}
        limiter.wait()
        budget.record_request()
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            return _parse_response(response.text)
        except Exception as exc:
            if _is_retryable(exc) and attempt < max_retries:
                logger.info(
                    "Gemini rate limit/transient error (attempt %d/%d) for a "
                    "batch of %d jobs; backing off %.0fs: %s",
                    attempt + 1, max_retries, len(jobs), backoff, exc,
                )
                time.sleep(backoff)
                backoff *= _BACKOFF_MULTIPLIER
                continue
            logger.warning(
                "Gemini keyword extraction failed for a batch of %d jobs: %s", len(jobs), exc
            )
            return {}
    return {}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class _DailyBudget:
    """Tracks actual Gemini network calls made today against
    requests_per_day -- counts each retry attempt individually, since a
    retry is a real request against the same quota, not a free do-over.
    Persists via db.get/set_state (if a db was passed) so the count holds
    across separate process runs on the same day, not just this call."""

    def __init__(self, db, limit: int) -> None:
        self._db = db
        self._limit = limit
        self._today = _today()
        self._count = self._load() if db is not None else 0

    def _load(self) -> int:
        raw = self._db.get_state(_DAILY_BUDGET_STATE_KEY)
        if not raw:
            return 0
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        return data.get("count", 0) if data.get("date") == self._today else 0

    @property
    def used(self) -> int:
        return self._count

    def exhausted(self) -> bool:
        return self._count >= self._limit

    def record_request(self) -> None:
        self._count += 1
        if self._db is not None:
            self._db.set_state(
                _DAILY_BUDGET_STATE_KEY, json.dumps({"date": self._today, "count": self._count})
            )


def extract_keywords(
    api_key: str | None,
    model: str,
    jobs: list[Job],
    batch_size: int = 10,
    requests_per_minute: int = 8,
    requests_per_day: int = 180,
    max_retries: int = 3,
    db=None,
) -> None:
    """Mutates each job's `jd_keywords` in place. Jobs with no description
    text are skipped (nothing to extract). No-op if `api_key` is unset.

    `db`, if passed, is used to persist how many Gemini requests have been
    made today (see Database.get_state/set_state) so the daily budget holds
    across separate runs (e.g. a scheduled run plus a manual re-run on the
    same day), not just within this one call. Without it, the budget is
    only enforced within this single call.
    """
    if not api_key:
        logger.info("Gemini API key not configured; skipping JD skill extraction")
        return

    extractable = [job for job in jobs if job.description.strip() and job.job_hash]
    if not extractable:
        return

    budget = _DailyBudget(db, requests_per_day)
    client = genai.Client(api_key=api_key)
    limiter = _RateLimiter(requests_per_minute)
    logger.info(
        "Extracting JD skills for %d jobs via Gemini (%s), batch size %d, "
        "%d/%d daily requests already used",
        len(extractable), model, batch_size, budget.used, requests_per_day,
    )
    for start in range(0, len(extractable), batch_size):
        if budget.exhausted():
            remaining = len(extractable) - start
            logger.warning(
                "Gemini daily request budget (%d) reached; leaving %d job(s) "
                "without Gemini-extracted skills for today -- ranking.py falls "
                "back to a plain substring scan for them",
                requests_per_day, remaining,
            )
            break

        batch = extractable[start : start + batch_size]
        results = _extract_one_batch(client, model, batch, limiter, max_retries, budget)
        for job in batch:
            job.jd_keywords = results.get(job.job_hash, [])
