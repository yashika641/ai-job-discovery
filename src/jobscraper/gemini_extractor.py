"""Gemini-based JD skill extraction: turns each job's free-text description
into a ranked list of the skills/technologies it actually requires (most ->
least emphasized in the text), so ranking.py can match against the
candidate's real skill set instead of only scanning for a fixed keyword
list. Batches multiple JDs into a single request to keep API call volume
down — this only ever runs on jobs that already passed the experience and
location hard filters in pipeline.py.

Never raises: any request/parse failure is logged and the affected jobs
simply keep an empty jd_keywords list, which ranking.py treats as "fall
back to the static substring match" (see ranking._match_skills). A Gemini
outage or missing API key must never break the daily run.
"""

from __future__ import annotations

import json
import logging

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


def _extract_one_batch(client: genai.Client, model: str, jobs: list[Job]) -> dict[str, list[str]]:
    prompt = _build_prompt(jobs)
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
        logger.warning("Gemini keyword extraction failed for a batch of %d jobs: %s", len(jobs), exc)
        return {}


def extract_keywords(api_key: str | None, model: str, jobs: list[Job], batch_size: int = 10) -> None:
    """Mutates each job's `jd_keywords` in place. Jobs with no description
    text are skipped (nothing to extract). No-op if `api_key` is unset."""
    if not api_key:
        logger.info("Gemini API key not configured; skipping JD skill extraction")
        return

    extractable = [job for job in jobs if job.description.strip() and job.job_hash]
    if not extractable:
        return

    client = genai.Client(api_key=api_key)
    logger.info(
        "Extracting JD skills for %d jobs via Gemini (%s), batch size %d",
        len(extractable), model, batch_size,
    )
    for start in range(0, len(extractable), batch_size):
        batch = extractable[start : start + batch_size]
        results = _extract_one_batch(client, model, batch)
        for job in batch:
            job.jd_keywords = results.get(job.job_hash, [])
