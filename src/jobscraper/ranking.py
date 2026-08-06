"""Relevance ranking: a weighted score (0-100) mapped to a 1-5 star rating,
plus a short human-readable "why it matches" string for the email report.

Weights:
    title match against target roles      0-40
    skill overlap (JD keywords x profile) 0-25
    remote fit                            0-15
    country/region fit                    0-10
    years-of-experience fit               0-10

Skill overlap uses job.jd_keywords — a ranked (most -> least important)
list extracted per-JD by gemini_extractor.py — matched against the
candidate's profile.preferred_keywords, weighted so a match on the JD's #1
skill counts for more than a match buried at #10. Falls back to a plain
substring scan of the job text when jd_keywords is empty (extraction
disabled, failed, or no description to extract from).

Note: pipeline.py's _qualifies_for_recommendation additionally gates on
this score crossing settings.email.min_score_for_recommendation. The
experience and location hard filters run *before* ranking (see
pipeline._within_experience_cap / _location_qualifies) — by the time a job
reaches here it has already cleared both, so YOE below is a ranking signal
only, not a second exclusion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobscraper.config import ProfileConfig, RolesConfig
from jobscraper.models import Job
from jobscraper.text_match import contains_term

# Unit covers "years"/"year", the "yrs"/"yr" abbreviation, and the "YOE"
# shorthand (word boundary at the end so it doesn't match inside a longer
# word). The separator between the number and unit allows a hyphen too, so
# adjective forms like "3-year experience requirement" match as well as
# "3 years" / "3+ yrs".
_YOE_UNIT = r"(?:years?|yrs?|yoe)\b"
_YOE_RANGE_RE = re.compile(rf"(\d+)\s*(?:-|to)\s*(\d+)\s*\+?\s*{_YOE_UNIT}", re.IGNORECASE)
_YOE_SINGLE_RE = re.compile(rf"(\d+)[\s-]*\+?\s*{_YOE_UNIT}", re.IGNORECASE)


@dataclass
class ScoreResult:
    score: float
    stars: int
    reason: str
    required_years_experience: int | None
    has_stack_overlap: bool


def score_job(job: Job, profile: ProfileConfig, roles: RolesConfig) -> ScoreResult:
    title_lower = job.title.lower()
    text = job.searchable_text()
    reasons: list[str] = []
    score = 0.0

    matched_role = next((r for r in roles.include if r.lower() in title_lower), None)
    if matched_role:
        score += 40
        reasons.append(f'title matches "{matched_role}"')

    matched_skills = _match_skills(job, profile, text)
    has_stack_overlap = bool(matched_skills)
    if has_stack_overlap:
        score += min(25.0, sum(_importance_weight(rank) for _, rank in matched_skills))
        shown = ", ".join(name for name, _ in matched_skills[:5])
        reasons.append(f"stack overlap: {shown}")

    if job.remote:
        if profile.remote_preference == "strong":
            score += 15
            reasons.append("fully remote")
        elif profile.remote_preference == "soft":
            score += 8
            reasons.append("remote")

    location_lower = job.location.lower()
    country_match = next(
        (c for c in profile.preferred_countries if c.lower() in location_lower), None
    )
    if country_match:
        score += 10
        reasons.append(f"location fits ({job.location})")
    elif job.remote:
        score += 5

    yoe_score, yoe_reason, required_years = _score_yoe(text, profile.max_years_experience)
    score += yoe_score
    if yoe_reason:
        reasons.append(yoe_reason)

    stars = _score_to_stars(score)
    reason_text = "; ".join(reasons) if reasons else "General AI/ML role match"
    return ScoreResult(score, stars, reason_text, required_years, has_stack_overlap)


def _match_skills(job: Job, profile: ProfileConfig, text: str) -> list[tuple[str, int]]:
    """Returns (skill name, importance rank) pairs, rank 1 = most important.

    Prefers job.jd_keywords — the Gemini-extracted, already
    importance-ordered list — matching each one against the candidate's
    preferred_keywords (either string containing the other, so "LLM" in the
    profile matches a JD keyword like "LLM fine-tuning"). Falls back to a
    plain substring scan of the job text, ranked by the profile list's own
    order, when no JD keywords were extracted."""
    if job.jd_keywords:
        matched = []
        for rank, jd_kw in enumerate(job.jd_keywords, start=1):
            if any(
                contains_term(pk, jd_kw) or contains_term(jd_kw, pk)
                for pk in profile.preferred_keywords
            ):
                matched.append((jd_kw, rank))
        return matched

    return [
        (kw, rank)
        for rank, kw in enumerate(profile.preferred_keywords, start=1)
        if contains_term(kw, text)
    ]


def _importance_weight(rank: int) -> float:
    """Rank 1 (most important JD skill) is worth 5 points, decaying to a
    floor of 1 point by rank 5+, so a handful of high-importance matches
    outweighs a long tail of minor ones (overall overlap score still capped
    at 25 in score_job)."""
    return max(1.0, 6.0 - rank)


def _score_to_stars(score: float) -> int:
    if score >= 80:
        return 5
    if score >= 65:
        return 4
    if score >= 50:
        return 3
    if score >= 30:
        return 2
    return 1


def _extract_min_years_required(text: str) -> int | None:
    """Finds the years-of-experience requirement mentioned in a job's title
    or description. For a range ("3-5 years") returns the lower bound —
    that's the actual minimum a candidate needs to qualify. Returns None if
    no YOE requirement is mentioned at all (checked before falling back to
    the single-number pattern, which would otherwise match part of a range
    like the "5" in "3-5 years")."""
    range_match = _YOE_RANGE_RE.search(text)
    if range_match:
        return int(range_match.group(1))

    single_match = _YOE_SINGLE_RE.search(text)
    if single_match:
        return int(single_match.group(1))

    return None


def extract_required_years(job: Job) -> int | None:
    """Public wrapper used by pipeline.py's hard experience filter, which
    runs before scoring — it needs the requirement independent of a full
    score_job pass."""
    return _extract_min_years_required(job.searchable_text())


def _score_yoe(text: str, max_years_experience: int) -> tuple[float, str, int | None]:
    """A job requiring at most `max_years_experience` (default 3) gets full
    marks. Beyond that the score decays on a gradient (2 points per year
    over, floored at 1) rather than dropping straight to a flat low score —
    a job needing 4 years should still outrank one needing 8. This is a
    ranking signal only: YOE never excludes a job from filtering.py. No YOE
    mentioned at all is neutral — we can't tell either way, so it shouldn't
    be penalized."""
    required = _extract_min_years_required(text)

    if required is None:
        return 5.0, "", None  # neutral half-credit when YOE isn't mentioned

    if required <= max_years_experience:
        return (
            10.0,
            f"needs ~{required} yrs experience (within your {max_years_experience}-yr preference)",
            required,
        )

    excess = required - max_years_experience
    score = max(1.0, 10.0 - 2.0 * excess)
    return (
        score,
        f"needs ~{required}+ yrs experience (above your {max_years_experience}-yr preference)",
        required,
    )


def rank_jobs(jobs: list[Job], profile: ProfileConfig, roles: RolesConfig) -> list[Job]:
    for job in jobs:
        result = score_job(job, profile, roles)
        job.rank_score = result.score
        job.rank_stars = result.stars
        job.rank_reason = result.reason
        job.required_years_experience = result.required_years_experience
        job.has_stack_overlap = result.has_stack_overlap
    return sorted(jobs, key=lambda j: j.rank_score, reverse=True)


def cap_per_company(jobs: list[Job], max_per_company: int) -> list[Job]:
    """Keeps at most `max_per_company` jobs per company, preferring the
    highest-ranked ones. Expects `jobs` to already be sorted by rank_score
    descending (as rank_jobs returns), so this keeps each company's best."""
    counts: dict[str, int] = {}
    capped: list[Job] = []
    for job in jobs:
        key = job.company_name.strip().lower()
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= max_per_company:
            capped.append(job)
    return capped
