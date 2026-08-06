"""Renders a ReportData into a Markdown report (also used as the plain-text
fallback body for the email)."""

from __future__ import annotations

from jobscraper.report import ReportData

_STAR_CHARS = {5: "★★★★★", 4: "★★★★", 3: "★★★", 2: "★★", 1: "★"}


def render_markdown(data: ReportData) -> str:
    lines = [
        f"# AI Job Digest — {data.run_date}",
        "",
        f"- **Total jobs found:** {data.total_jobs_found}",
        f"- **New jobs today:** {data.new_jobs_found}",
        f"- **Companies checked:** {data.companies_checked} "
        f"({data.companies_failed} failed/unreachable)",
        f"- **Global sources checked:** {data.sources_checked} "
        f"({data.sources_failed} failed/unreachable)",
        "",
        f"## Top {len(data.recommendations)} Recommendations",
        "",
    ]

    if not data.recommendations:
        lines.append("_No new jobs met the ranking threshold today._")
    else:
        for i, job in enumerate(data.recommendations, start=1):
            stars = _STAR_CHARS.get(job.rank_stars, "")
            lines.extend(
                [
                    f"### {i}. {job.title} — {job.company_name} {stars}",
                    f"- **Location:** {job.location or 'n/a'}"
                    f" ({'Remote' if job.remote else 'On-site'})",
                    f"- **Apply:** {job.apply_url}",
                    f"- **Why it matches:** {job.rank_reason}",
                    f"- **ATS platform:** {job.ats_platform or 'n/a'}",
                    f"- **Posted:** {job.posted_date or 'n/a'}",
                    f"- **Priority:** {job.company_priority or 'n/a'} "
                    f"(score {job.rank_score:.0f}/100)",
                    "",
                ]
            )

    if data.worth_looking_at:
        lines.extend(
            [
                f"## Other jobs worth looking at ({len(data.worth_looking_at)})",
                "",
                "Every other new job that passed the skills filter this run, ranked by "
                "score — didn't clear the recommendation threshold, isn't in a qualifying "
                "location, or has no direct stack overlap. Reason noted per job:",
                "",
            ]
        )
        for job in data.worth_looking_at:
            stars = _STAR_CHARS.get(job.rank_stars, "")
            lines.append(
                f"- {job.title} — {job.company_name} {stars} — {job.apply_url}\n"
                f"  _{job.rank_reason}_"
            )
        lines.append("")

    return "\n".join(lines)
