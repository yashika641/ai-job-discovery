"""Orchestrates one full daily run:

    fetch -> categorical exclude -> hard experience filter -> hard location
    filter -> hard age filter -> dedupe -> Gemini skill extraction (new jobs
    only) -> score -> skill-overlap filter -> store -> report -> email

This is the single entrypoint scripts/run_daily.py calls; nothing here
should be GitHub-Actions-specific.

Two independent fetch passes feed the same pipeline:
  - Company pass: per-company ATS scraping against data/companies.csv
    (curated, genuine ATS provider + identifier per company — see
    scripts/detect_ats.py). This is the high-volume source. It has no
    incremental cursor -- each run re-scrapes whatever's currently live on
    every career page -- so the age filter below is what actually keeps
    stale postings out, not just dedupe.
  - Global pass: cross-company aggregators (RemoteOK, WeWorkRemotely,
    Himalayas, Jobicy) that need no company list, date-windowed via
    `since` (see _compute_since) so repeat runs only look at what's new.

The experience, location, and age filters run as hard, early cuts
specifically so that the (paid, rate-limited) Gemini skill-extraction step
only ever runs on jobs that already look like plausible candidates — see
gemini_extractor.py.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobscraper import gemini_extractor
from jobscraper.companies import load_companies
from jobscraper.company_fetcher import fetch_company_jobs
from jobscraper.config import Settings
from jobscraper.db import Database
from jobscraper.dedupe import dedupe_and_mark
from jobscraper.email_sender import send_email
from jobscraper.filtering import filter_jobs
from jobscraper.http_client import HttpClient
from jobscraper.models import Job, utcnow_iso
from jobscraper.ranking import cap_per_company, extract_required_years, rank_jobs
from jobscraper.report import ReportData
from jobscraper.report.html import render_html
from jobscraper.report.markdown import render_markdown
from jobscraper.sources import himalayas, jobicy, remoteok, weworkremotely
from jobscraper.sources.date_utils import parse_posted_at
from jobscraper.text_match import contains_term

logger = logging.getLogger(__name__)

_LAST_RUN_STATE_KEY = "last_run_completed_at"

# Whitelist, not blacklist: source location fields are free text ("Remote -
# United States", "Remote (New York)", "SF Office" with remote=True, ...)
# and a blacklist of non-India country/city names is unbounded — it missed
# "Remote (New York)" and "SF Office" in testing precisely because neither
# names a *country*. So instead: a remote job qualifies only if it's
# explicitly India/worldwide, OR the location says nothing more specific
# than "remote" at all (nothing to disqualify it on). Any other location
# detail alongside "remote" that isn't India/worldwide is treated as a
# regional restriction, recognized or not.
_INDIA_OR_WORLDWIDE_MARKERS = [
    "india", "worldwide", "global", "anywhere", "international",
    # Major Indian cities/hubs, so "Remote, Bangalore" etc. still qualify
    # without requiring the literal word "India".
    "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune",
    "chennai", "kolkata", "ahmedabad", "gurgaon", "gurugram", "noida",
]
_BARE_REMOTE_RE = re.compile(r"^\s*remote\s*$", re.IGNORECASE)


def _within_experience_cap(job: Job, max_years: int) -> bool:
    """Hard experience filter, applied before location filtering and
    (crucially) before the paid Gemini extraction step — a job asking for
    more than `max_years` is dropped entirely rather than merely scored
    down. Jobs that don't mention YOE at all always pass; silence isn't
    disqualifying."""
    required = extract_required_years(job)
    return required is None or required <= max_years


def _within_age_limit(job: Job, max_age_days: int | None, now: datetime) -> bool:
    """Hard freshness filter, applied to every job (company-ATS scrapes and
    global-source jobs alike) before the paid Gemini extraction step, same
    as the experience/location filters. Company ATS pages have no
    incremental cursor -- unlike the global sources' `since` -- so without
    this, a listing that's been live for months would pass straight
    through every run. Reuses date_utils.parse_posted_at so it understands
    every source/ATS's raw posted-date format. A disabled limit
    (max_age_days=None) or a missing/unparseable posted_date always passes
    -- several ATS adapters (BambooHR, generic HTML, Teamtailor, Workday's
    relative "Posted X Days Ago" strings) never give us a parseable date,
    and silence isn't evidence a job is stale."""
    if max_age_days is None:
        return True
    posted_at = parse_posted_at(job.posted_date)
    if posted_at is None:
        return True
    return posted_at >= now - timedelta(days=max_age_days)


def _is_qualifying_remote(job: Job) -> bool:
    """A remote job qualifies if it's explicitly India/worldwide, or if its
    location says nothing more specific than the word "remote" (or nothing
    at all); any other location detail is treated as a regional restriction
    (see profile: "for remote jobs specifically look for india remote or
    worldwide remote jobs")."""
    if not job.remote:
        return False
    location = job.location
    if any(contains_term(marker, location) for marker in _INDIA_OR_WORLDWIDE_MARKERS):
        return True
    return not location.strip() or bool(_BARE_REMOTE_RE.match(location))


def _is_onsite_hub(job: Job, hub_cities: list[str]) -> bool:
    """On-site/hybrid jobs physically located in a configured hub city
    (Gurgaon/Noida by default) qualify without needing to be remote."""
    return any(contains_term(city, job.location) for city in hub_cities)


def _location_qualifies(job: Job, hub_cities: list[str]) -> bool:
    """Hard location filter, applied before Gemini extraction: India/
    worldwide remote, or on-site/hybrid in a configured hub city. Other
    on-site Indian cities still need to be remote to pass (see
    profile.onsite_hub_cities)."""
    return _is_qualifying_remote(job) or _is_onsite_hub(job, hub_cities)


def _qualifies_for_recommendation(job: Job, min_score: float) -> bool:
    """By the time a job reaches this point it has already cleared the
    experience filter, the location filter, and the skill-overlap filter
    (see run_pipeline) — the only remaining question for "Top
    Recommendations" vs. "worth looking at" is whether its score clears the
    recommendation bar."""
    return job.rank_score >= min_score


def _not_recommended_reason(job: Job, min_score: float) -> str:
    """Explains why a job landed in "worth looking at" instead of the main
    list, so it's a visible classification, not a silent drop."""
    return f"score {job.rank_score:.0f} below your {min_score:.0f}-point recommendation threshold"


def _compute_since(db: Database, settings: Settings) -> tuple[datetime, bool]:
    """First run (nothing recorded yet): look back a few days. Every run
    after that: only since the previous run completed."""
    now = datetime.now(timezone.utc)
    last_run_raw = db.get_state(_LAST_RUN_STATE_KEY)
    if last_run_raw is None:
        since = now - timedelta(days=settings.sources.first_run_lookback_days)
        return since, True
    return datetime.fromisoformat(last_run_raw), False


def _run_company_pass(
    client: HttpClient, db: Database, settings: Settings, company_limit: int | None
) -> tuple[list[Job], int, int]:
    companies = load_companies(settings.sources.companies_csv)
    if company_limit is not None:
        companies = companies[:company_limit]

    # Upsert every company up front so companies table stays complete even
    # if the run is interrupted mid-way through fetching.
    company_ids = {c.name: db.upsert_company(c) for c in companies}

    all_jobs: list[Job] = []
    companies_failed = 0

    logger.info(
        "Checking %d companies (concurrency=%d)", len(companies), settings.http.concurrency
    )
    with ThreadPoolExecutor(max_workers=settings.http.concurrency) as pool:
        futures = {
            pool.submit(fetch_company_jobs, client, company, settings.roles.include): company
            for company in companies
        }
        for future in as_completed(futures):
            company = futures[future]
            company_id = company_ids[company.name]
            try:
                result = future.result()
            except Exception as exc:
                logger.warning("Unexpected failure for %s: %s", company.name, exc)
                db.record_company_check(company_id, None, None, f"error: {exc}")
                companies_failed += 1
                continue

            db.record_company_check(
                company_id, result.ats_provider, result.ats_identifier, result.status
            )
            if result.status.startswith("error"):
                companies_failed += 1

            for job in result.jobs:
                job.company_id = company_id
                job.company_priority = company.priority
            all_jobs.extend(result.jobs)

    logger.info(
        "Company pass done: %d jobs found, %d companies failed",
        len(all_jobs),
        companies_failed,
    )
    return all_jobs, len(companies), companies_failed


def _run_global_sources_pass(
    client: HttpClient, settings: Settings, since: datetime
) -> tuple[list[Job], int, int]:
    calls: list[tuple[str, Callable[[], list[Job]]]] = []

    for rss in settings.sources.rss:
        if rss.name == "weworkremotely":
            calls.append(
                ("weworkremotely", lambda url=rss.url: weworkremotely.fetch_jobs(client, url, since=since))
            )
        else:
            logger.warning("No adapter registered for RSS source %r", rss.name)

    remoteok_url = settings.sources.apis.get("remoteok")
    if remoteok_url:
        calls.append(
            ("remoteok", lambda url=remoteok_url: remoteok.fetch_jobs(client, url, since=since))
        )

    if "himalayas" in settings.sources.apis:
        calls.append(
            ("himalayas", lambda: himalayas.fetch_jobs(client, settings.roles.include, since=since))
        )

    if "jobicy" in settings.sources.apis:
        calls.append(("jobicy", lambda: jobicy.fetch_jobs(client, since=since)))

    jobs: list[Job] = []
    sources_failed = 0
    for name, call in calls:
        try:
            jobs.extend(call())
        except Exception as exc:
            logger.warning("Unexpected failure from source %s: %s", name, exc)
            sources_failed += 1

    logger.info(
        "Global source pass done: %d jobs found across %d sources (%d failed)",
        len(jobs), len(calls), sources_failed,
    )
    return jobs, len(calls), sources_failed


def run_pipeline(
    settings: Settings,
    db: Database,
    company_limit: int | None = None,
    send_email_flag: bool = True,
) -> ReportData:
    client = HttpClient(
        timeout_seconds=settings.http.timeout_seconds,
        max_retries=settings.http.max_retries,
        backoff_seconds=settings.http.backoff_seconds,
        rate_limit_per_host_seconds=settings.http.rate_limit_per_host_seconds,
        user_agent=settings.http.user_agent,
    )

    company_jobs, companies_checked, companies_failed = _run_company_pass(
        client, db, settings, company_limit
    )

    since, is_first_run = _compute_since(db, settings)
    logger.info(
        "Fetching global-source jobs posted since %s (%s)",
        since.isoformat(), "first run, lookback window" if is_first_run else "since last run",
    )
    global_jobs, sources_checked, sources_failed = _run_global_sources_pass(client, settings, since)

    all_jobs = company_jobs + global_jobs

    # Stage 1: categorical exclude (marketing/sales/HR/... and internships
    # unless enabled) — cheap, title-only, no API calls involved.
    candidate_jobs = filter_jobs(all_jobs, settings.roles)
    logger.info("%d jobs after categorical exclude", len(candidate_jobs))

    # Stage 2: hard experience filter — drop jobs asking for more than the
    # configured ceiling before spending anything further on them.
    max_years_hard_limit = settings.profile.max_years_experience_hard_limit
    candidate_jobs = [j for j in candidate_jobs if _within_experience_cap(j, max_years_hard_limit)]
    logger.info("%d jobs after experience filter (<= %d yrs)", len(candidate_jobs), max_years_hard_limit)

    # Stage 3: hard location filter — India/worldwide remote, or on-site/
    # hybrid in a configured hub city (Gurgaon/Noida by default).
    hub_cities = settings.profile.onsite_hub_cities
    candidate_jobs = [j for j in candidate_jobs if _location_qualifies(j, hub_cities)]
    logger.info("%d jobs after location filter", len(candidate_jobs))

    # Stage 3b: hard freshness filter — drops postings older than
    # sources.max_job_age_days. This is what actually bounds the age of
    # company-ATS jobs (that pass has no incremental cursor); for global
    # sources it's mostly a no-op in steady state since `since` already
    # keeps them recent, except on a first run's wider lookback window.
    max_job_age_days = settings.sources.max_job_age_days
    now = datetime.now(timezone.utc)
    candidate_jobs = [j for j in candidate_jobs if _within_age_limit(j, max_job_age_days, now)]
    logger.info("%d jobs after age filter (<= %s days)", len(candidate_jobs), max_job_age_days)

    applied_hashes = db.all_applied_hashes()
    known_hashes = db.all_known_hashes()
    deduped_jobs = dedupe_and_mark(candidate_jobs, known_hashes, applied_hashes)
    logger.info("%d jobs after dedupe", len(deduped_jobs))

    # Stage 4: Gemini skill extraction, batched, restricted to brand-new
    # postings — already-seen jobs don't need re-analysis, which keeps API
    # usage bounded to what's actually new each day. Rate/daily-budget
    # limits come from settings.gemini (see config.yaml) and are enforced
    # inside extract_keywords; `db` is passed so the daily request count
    # persists across separate runs on the same day.
    new_for_extraction = [j for j in deduped_jobs if j.is_new]
    if settings.gemini.enabled:
        gemini_extractor.extract_keywords(
            settings.gemini.api_key,
            settings.gemini.model,
            new_for_extraction,
            settings.gemini.batch_size,
            settings.gemini.requests_per_minute,
            settings.gemini.requests_per_day,
            settings.gemini.max_retries,
            db,
        )

    # Stage 5: score everything (skill match now uses each job's
    # Gemini-extracted, importance-ranked JD keywords when available,
    # falling back to a static scan otherwise — see ranking._match_skills),
    # then apply the actual relevance gate: zero skill overlap = dropped.
    scored_jobs = rank_jobs(deduped_jobs, settings.profile, settings.roles)
    relevant_jobs = [j for j in scored_jobs if j.has_stack_overlap]
    relevant_jobs = cap_per_company(relevant_jobs, settings.limits.max_jobs_per_company)
    logger.info("%d jobs after skill-overlap filtering", len(relevant_jobs))
    for job in relevant_jobs:
        db.upsert_job(job)

    # Every new job that made it through every filter is reported, ranked
    # by score, with no count cap — recommendations vs. "worth looking at"
    # is purely a score classification, not a truncation.
    new_jobs = [j for j in relevant_jobs if j.is_new]
    min_score = settings.email.min_score_for_recommendation
    recommendations = [j for j in new_jobs if _qualifies_for_recommendation(j, min_score)]

    recommended_hashes = {j.job_hash for j in recommendations}
    worth_looking_at: list[Job] = []
    for j in new_jobs:
        if j.job_hash in recommended_hashes:
            continue
        # Overwrite rank_reason for display in this report only — already
        # upserted to the DB above, so this doesn't touch stored history.
        j.rank_reason = _not_recommended_reason(j, min_score)
        worth_looking_at.append(j)

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_data = ReportData(
        run_date=run_date,
        total_jobs_found=len(relevant_jobs),
        new_jobs_found=len(new_jobs),
        companies_checked=companies_checked,
        companies_failed=companies_failed,
        sources_checked=sources_checked,
        sources_failed=sources_failed,
        recommendations=recommendations,
        worth_looking_at=worth_looking_at,
    )

    output_dir = Path(settings.report.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / f"{run_date}.html"
    md_path = output_dir / f"{run_date}.md"

    html_body = render_html(report_data)
    md_body = render_markdown(report_data)
    html_path.write_text(html_body, encoding="utf-8")
    md_path.write_text(md_body, encoding="utf-8")

    email_sent = False
    if send_email_flag and settings.email.enabled:
        sender, password = settings.email.sender, settings.email.password
        if not sender or not password:
            logger.warning("Email credentials not configured (env vars unset); skipping send")
        else:
            subject = f"AI Job Digest — {run_date} — {len(new_jobs)} new opportunities"
            email_sent = send_email(
                settings.email.smtp_host,
                settings.email.smtp_port,
                sender,
                password,
                settings.email.recipient,
                subject,
                html_body,
                md_body,
            )

    db.record_daily_report(
        run_date=run_date,
        total_jobs_found=report_data.total_jobs_found,
        new_jobs_found=report_data.new_jobs_found,
        companies_checked=report_data.companies_checked,
        companies_failed=report_data.companies_failed,
        sources_checked=report_data.sources_checked,
        sources_failed=report_data.sources_failed,
        report_html_path=str(html_path),
        report_md_path=str(md_path),
        email_sent=email_sent,
    )
    db.set_state(_LAST_RUN_STATE_KEY, utcnow_iso())

    return report_data
