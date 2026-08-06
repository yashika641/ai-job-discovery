"""Core data models shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Company:
    name: str
    career_page: str
    region: str = ""
    priority: str = ""
    notes: str = ""
    ats_provider: str | None = None
    ats_identifier: str | None = None
    last_checked_at: str | None = None
    last_status: str | None = None


@dataclass
class Job:
    """A single normalized job posting, regardless of where it came from."""

    company_name: str
    title: str
    apply_url: str
    source: str                      # "company_ats" | "remoteok" | "weworkremotely"
    location: str = ""
    remote: bool = False
    ats_platform: str | None = None  # "greenhouse" | "lever" | ... | "generic" | None
    posted_date: str | None = None
    description: str = ""
    company_id: int | None = None
    company_priority: str = ""       # Company.priority (e.g. "Tier 1"), blank for global sources

    # populated later in the pipeline
    job_hash: str = ""
    rank_score: float = 0.0
    rank_stars: int = 0
    rank_reason: str = ""
    is_new: bool = True
    required_years_experience: int | None = None  # extracted from text; None if not mentioned
    has_stack_overlap: bool = False               # matched >=1 profile.preferred_keywords
    # Skills/technologies the JD actually asks for, most -> least important,
    # as extracted by Gemini (see gemini_extractor.py). Empty until that
    # step runs (or if it's disabled/fails), in which case ranking.py falls
    # back to a plain substring scan of the job text.
    jd_keywords: list[str] = field(default_factory=list)

    def searchable_text(self) -> str:
        return f"{self.title}\n{self.location}\n{self.description}".lower()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
