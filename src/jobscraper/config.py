"""Loads config/config.yaml into typed, validated settings objects."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class ProfileConfig(BaseModel):
    # Jobs requiring at most this many years of experience score best;
    # jobs requiring more (but still within max_years_experience_hard_limit)
    # score lower on a gradient (see ranking._score_yoe) rather than being
    # excluded outright.
    max_years_experience: int = 3
    # Hard cutoff applied before a job is even sent to Gemini for skill
    # extraction (see pipeline._within_experience_cap): jobs asking for more
    # than this many years are dropped entirely. Jobs that don't mention YOE
    # at all always pass this filter — silence isn't a red flag.
    max_years_experience_hard_limit: int = 5
    preferred_countries: list[str] = []
    remote_preference: str = "soft"
    # The candidate's actual skill set. Matched against each job's
    # Gemini-extracted keywords (or, as a fallback, a plain substring scan
    # of the job text) to decide relevance and score.
    preferred_keywords: list[str] = []
    # A job must overlap on at least this many preferred_keywords (after
    # skill matching) to be kept — the sole relevance gate; see pipeline.py.
    min_skill_matches: int = 1
    # On-site/hybrid jobs located in one of these cities qualify for
    # recommendations even when not remote (see pipeline._location_qualifies).
    onsite_hub_cities: list[str] = ["Gurgaon", "Gurugram", "Noida"]


class GeminiConfig(BaseModel):
    # Extracts each new job's actual required skills (ranked by importance)
    # from its JD text via the Gemini API, so scoring can match against the
    # candidate's real skill set instead of only a fixed keyword scan (see
    # gemini_extractor.py). Runs only on jobs that already passed the
    # experience and location hard filters, to keep API usage bounded.
    enabled: bool = True
    api_key_env_var: str = "GEMINI_API_KEY"
    model: str = "gemini-3.5-flash"
    # Number of job descriptions bundled into a single API request. Higher
    # = fewer requests (helps RPM/RPD) at the cost of a bigger prompt (TPM)
    # and a bigger blast radius if one batch's response fails to parse (see
    # gemini_extractor._parse_response) -- 20 keeps prompts a few thousand
    # tokens, far under free-tier TPM, while roughly halving request count
    # versus the old batch_size=10.
    batch_size: int = 20
    # Free-tier throttling: these default to conservative numbers because
    # Google doesn't publish one fixed guaranteed figure -- actual limits
    # vary by model/account and change over time. Check your real numbers
    # at https://aistudio.google.com/rate-limit and tighten/loosen these to
    # match. gemini_extractor.py sleeps as needed to stay under
    # requests_per_minute, and stops calling the API for the rest of the run
    # once requests_per_day is hit (remaining jobs fall back to the plain
    # substring skill scan rather than erroring out).
    requests_per_minute: int = 8
    requests_per_day: int = 180
    # Retries on a 429/RESOURCE_EXHAUSTED response, with exponential
    # backoff, before giving up on a batch and falling back for just those
    # jobs -- see gemini_extractor._is_retryable.
    max_retries: int = 3

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env_var)


class RolesConfig(BaseModel):
    # Search-time discovery keywords (generic HTML career-page scraping,
    # Himalayas query) and a title-match scoring bonus in ranking.py. NOT a
    # relevance filter — filtering.py gates purely on skill overlap.
    include: list[str] = []
    exclude_keywords: list[str] = []
    include_internships: bool = False
    internship_keywords: list[str] = []


class RssSource(BaseModel):
    name: str
    url: str


class SourcesConfig(BaseModel):
    companies_csv: str = "data/companies.csv"
    rss: list[RssSource] = []
    apis: dict[str, str] = {}
    # On the very first run (no prior run recorded), fetch jobs posted in the
    # last N days. Every run after that only fetches since the previous run.
    first_run_lookback_days: int = 3
    # Hard freshness filter applied to every job -- company-ATS scrapes
    # included (see pipeline._within_age_limit) -- before Gemini extraction.
    # A posting older than this many days is dropped outright. Set to null
    # to disable. A missing/unparseable posted_date always passes -- not
    # every ATS adapter exposes one.
    max_job_age_days: int | None = 30


class HttpConfig(BaseModel):
    timeout_seconds: int = 15
    max_retries: int = 3
    backoff_seconds: int = 2
    concurrency: int = 10
    rate_limit_per_host_seconds: float = 1.0
    user_agent: str = "Mozilla/5.0 (compatible; AIJobDiscoveryBot/1.0)"


class ScheduleConfig(BaseModel):
    cron: str = "30 2 * * *"


class EmailConfig(BaseModel):
    enabled: bool = True
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_env_var: str = "EMAIL_SENDER"
    password_env_var: str = "EMAIL_APP_PASSWORD"
    recipient: str = ""
    # Jobs scoring at or above this land in "Top Recommendations"; every
    # other new job that passed the skills filter is still listed, in full
    # and ranked by score, under "Other jobs worth looking at" — no count
    # caps on either section.
    min_score_for_recommendation: float = 70.0

    @property
    def sender(self) -> str | None:
        return os.environ.get(self.sender_env_var)

    @property
    def password(self) -> str | None:
        return os.environ.get(self.password_env_var)


class LimitsConfig(BaseModel):
    max_jobs_per_company: int = 5


class StorageConfig(BaseModel):
    db_path: str = "data/jobscraper.db"


class ReportConfig(BaseModel):
    output_dir: str = "reports"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/jobscraper.log"


class Settings(BaseModel):
    profile: ProfileConfig = ProfileConfig()
    gemini: GeminiConfig = GeminiConfig()
    roles: RolesConfig = RolesConfig()
    sources: SourcesConfig = SourcesConfig()
    http: HttpConfig = HttpConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    email: EmailConfig = EmailConfig()
    limits: LimitsConfig = LimitsConfig()
    storage: StorageConfig = StorageConfig()
    report: ReportConfig = ReportConfig()
    logging: LoggingConfig = LoggingConfig()


def load_settings(config_path: str | Path = "config/config.yaml") -> Settings:
    path = Path(config_path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw)
