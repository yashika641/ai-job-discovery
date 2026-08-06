"""Categorical exclude filter, applied before the experience/location hard
filters in pipeline.py. This only ever drops jobs whose title names a
function that's never a fit regardless of skills (marketing, sales, HR,
...) or internships (unless enabled) — it does NOT decide relevance by
title or skill match. Actual relevance is decided later, after Gemini skill
extraction, by ranking.has_stack_overlap (see pipeline.run_pipeline).
"""

from __future__ import annotations

from jobscraper.config import RolesConfig
from jobscraper.models import Job
from jobscraper.text_match import contains_term


def is_relevant(job: Job, roles: RolesConfig) -> bool:
    for excluded in roles.exclude_keywords:
        if contains_term(excluded, job.title):
            return False

    if not roles.include_internships:
        for kw in roles.internship_keywords:
            if contains_term(kw, job.title):
                return False

    return True


def filter_jobs(jobs: list[Job], roles: RolesConfig) -> list[Job]:
    return [job for job in jobs if is_relevant(job, roles)]
