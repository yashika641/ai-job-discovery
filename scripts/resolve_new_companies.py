#!/usr/bin/env python
"""One-off: resolve real ATS identifiers for data/companies_sample - Sheet1.csv
(Company, ATS free-text only — no career page URL, no board token) and write a
new data/companies.csv with only LIVE-VERIFIED (provider, identifier) pairs.

Strategy per company:
  1. Classify the free-text ATS column to zero or more supported providers
     (substring match against greenhouse/lever/ashby/workable/smartrecruiters/
     workday). Rows naming only unsupported platforms (Keka, Darwinbox, Zoho
     Recruit, SAP SuccessFactors, Rippling, "Not Accessible", blank, ...) are
     skipped entirely — no adapter exists for them.
  2. Seed candidate identifiers from the previously-verified (deleted)
     data/companies.csv (recovered via `git show HEAD:data/companies.csv`) by
     exact company-name match, regardless of which provider it was filed
     under — ATS providers migrate, so a name match is worth checking even
     against a different provider than the new sheet claims.
  3. Generate slug variants from the company name for every remaining
     candidate provider (skipped for workday — a wd-instance number can't be
     guessed, only reused-from-old or found separately).
  4. Every candidate (reused or guessed) is verified with a live HTTP call to
     the adapter's real API before being accepted. A wrong guess just 404s;
     nothing goes into the output CSV without a real 200 + parseable board.

Usage:
    python scripts/resolve_new_companies.py
    python scripts/resolve_new_companies.py --workers 12
    python scripts/resolve_new_companies.py --new-only          # only verify names not already in companies.csv
    python scripts/resolve_new_companies.py --retry-unresolved --brute-force --workers 25
        # re-attempt everything in reports/unresolved_companies.csv, trying every
        # guessable provider per company regardless of the sheet's stated ATS hint;
        # never touches or re-verifies rows already in data/companies.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = REPO_ROOT / "data" / "companies_sample - Sheet1.csv"
OUT_CSV = REPO_ROOT / "data" / "companies.csv"
UNRESOLVED_CSV = REPO_ROOT / "reports" / "unresolved_companies.csv"

_SUPPORTED = ["greenhouse", "lever", "ashby", "workday", "workable", "smartrecruiters",
              "teamtailor", "bamboohr", "recruitee"]
# Providers whose identifier is a guessable company-name slug (as opposed to
# workday/oracle_recruiting, whose identifier embeds a real tenant/instance
# number nothing about the company name can predict).
_GUESSABLE = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters",
              "teamtailor", "bamboohr", "recruitee"]

_ALIASES = {
    "bamboohr": ["bamboohr", "bamboo hr"],
    "recruitee": ["recruitee", "tellent"],
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def classify_providers(ats_raw: str) -> list[str]:
    text = (ats_raw or "").strip().lower()
    found = []
    for p in _SUPPORTED:
        if any(alias in text for alias in _ALIASES.get(p, [p])):
            found.append(p)
    return found


def _clean_name(name: str) -> str:
    """Strip parenthetical asides and '/'-separated alt-names so slug
    generation targets the primary company name, e.g. 'Poolside (EU team)'
    -> 'Poolside', 'UnitedHealth Group / Optum' -> 'UnitedHealth Group'."""
    cleaned = re.sub(r"\([^)]*\)", "", name)
    cleaned = cleaned.split("/")[0]
    return cleaned.strip() or name


def slug_variants(name: str) -> list[str]:
    name = _clean_name(name)
    lower = name.lower().replace("&", "and")
    stripped = re.sub(r"[^a-z0-9\s-]", "", lower)
    words = stripped.split()
    no_space = "".join(words)
    hyphen = "-".join(words)
    first_word = words[0] if words else no_space
    first_two = "".join(words[:2]) if len(words) >= 2 else no_space
    first_two_hyphen = "-".join(words[:2]) if len(words) >= 2 else hyphen

    drop_suffixes = {"ai", "labs", "inc", "technologies", "technology", "software",
                      "systems", "india", "tech", "analytics", "solutions", "co",
                      "group", "global", "international", "corp", "corporation"}
    trimmed_words = [w for w in words if w not in drop_suffixes]
    trimmed = "".join(trimmed_words) if trimmed_words else no_space
    trimmed_hyphen = "-".join(trimmed_words) if trimmed_words else hyphen

    variants = [
        no_space, hyphen, trimmed, trimmed_hyphen, first_word, first_two, first_two_hyphen,
        f"{no_space}ai", f"{trimmed}ai", f"get{no_space}", f"get{trimmed}",
        f"{no_space}hq", f"try{no_space}", f"{no_space}inc", f"{no_space}labs",
        f"{no_space}app", f"the{no_space}", f"join{no_space}",
        f"{no_space}io", f"{trimmed}io", f"use{no_space}", f"use{trimmed}",
        f"{no_space}careers", f"{no_space}jobs", f"we{no_space}",
        f"{first_word}hq",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            ordered.append(v)
    return ordered


def build_url(provider: str, identifier: str) -> str:
    if provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs"
    if provider == "lever":
        return f"https://api.lever.co/v0/postings/{identifier}?mode=json"
    if provider == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{identifier}"
    if provider == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{identifier}"
    if provider == "smartrecruiters":
        return f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings"
    if provider == "workday":
        try:
            host_part, site = identifier.split("/", 1)
            tenant = host_part.split(".")[0]
        except ValueError:
            return ""
        return f"https://{host_part}/wday/cxs/{tenant}/{site}/jobs"
    if provider == "teamtailor":
        return f"https://{identifier}.teamtailor.com/jobs"
    if provider == "bamboohr":
        return f"https://{identifier}.bamboohr.com/careers/list"
    if provider == "recruitee":
        return f"https://{identifier}.recruitee.com/api/offers/"
    return ""


def build_career_url(provider: str, identifier: str) -> str:
    """A human-facing career-page URL derived from the verified identifier.
    load_companies() requires a non-blank URL per row; company_fetcher never
    actually fetches it when ats_provider+ats_identifier are cached (see
    fetch_company_jobs), so this only needs to be a real, clickable link —
    not something the pipeline depends on for correctness."""
    if provider == "greenhouse":
        return f"https://boards.greenhouse.io/{identifier}"
    if provider == "lever":
        return f"https://jobs.lever.co/{identifier}"
    if provider == "ashby":
        return f"https://jobs.ashbyhq.com/{identifier}"
    if provider == "workable":
        return f"https://apply.workable.com/{identifier}"
    if provider == "smartrecruiters":
        return f"https://careers.smartrecruiters.com/{identifier}"
    if provider == "workday":
        return f"https://{identifier}"
    if provider == "teamtailor":
        return f"https://{identifier}.teamtailor.com/jobs"
    if provider == "bamboohr":
        return f"https://{identifier}.bamboohr.com/careers"
    if provider == "recruitee":
        return f"https://{identifier}.recruitee.com"
    return ""


_TEAMTAILOR_JOB_RE = re.compile(r"/jobs/\d+")


def verify(provider: str, identifier: str) -> int | None:
    """Returns the job count if the identifier is real and live, else None."""
    url = build_url(provider, identifier)
    if not url:
        return None
    try:
        if provider == "workday":
            resp = requests.post(
                url, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                headers={**_HEADERS, "Content-Type": "application/json"}, timeout=10,
            )
        elif provider == "bamboohr":
            resp = requests.get(url, headers={**_HEADERS, "Accept": "application/json"}, timeout=10)
        else:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        if provider == "teamtailor":
            # No public JSON API — a real tenant's /jobs page lists postings as
            # /jobs/<id> links; a fake subdomain either 404s (caught above) or
            # resolves to Teamtailor's own generic landing page with none.
            count = len(set(_TEAMTAILOR_JOB_RE.findall(resp.text)))
            return count if count > 0 else None
        data = resp.json()
    except Exception:
        return None

    # Every provider below returns real HTTP 200 + a well-formed (if empty)
    # jobs list/array for ANY syntactically valid slug — greenhouse, lever,
    # ashby, workable, bamboohr and recruitee accounts are trivial to create,
    # so a *guessed* slug that happens to belong to some unrelated real
    # account is common, especially once slug guessing gets aggressive
    # (short/common words collide). A 200 + empty list is therefore no
    # stronger evidence than a 404: only a NONZERO list proves the guessed
    # identifier actually belongs to the company being searched for, so
    # empty results are rejected the same way a real 404 would be.
    list_key = {
        "recruitee": "offers", "bamboohr": "result", "greenhouse": "jobs",
        "ashby": "jobs", "workable": "jobs", "smartrecruiters": "content",
        "workday": "jobPostings",
    }.get(provider)
    if provider == "lever":
        items = data if isinstance(data, list) else None
    elif list_key is not None:
        items = data.get(list_key) if isinstance(data, dict) else None
    else:
        return None
    if not isinstance(items, list) or len(items) == 0:
        return None
    return len(items)


def load_old_identifiers() -> dict[str, list[tuple[str, str]]]:
    """name.lower() -> [(provider, identifier), ...] from the previously
    committed companies.csv (recovered from git even though the working tree
    copy was deleted)."""
    import subprocess

    try:
        raw = subprocess.run(
            ["git", "show", "HEAD:data/companies.csv"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True, encoding="utf-8",
        ).stdout
    except Exception:
        return {}

    import io
    reader = csv.DictReader(io.StringIO(raw))
    result: dict[str, list[tuple[str, str]]] = {}
    for row in reader:
        name = (row.get("Company") or "").strip()
        provider = (row.get("ATS") or "").strip()
        identifier = (row.get("ATS Identifier") or "").strip()
        if name and provider and identifier:
            result.setdefault(name.lower(), []).append((provider, identifier))
    return result


def resolve_one(
    company: str, stated_providers: list[str], old_identifiers: dict[str, list[tuple[str, str]]],
    brute_force: bool = False,
) -> tuple[str, str, str, int] | None:
    """Returns (company, provider, identifier, job_count) on success, else None.
    In brute_force mode, every guessable provider is tried (not just the ones
    the sample sheet's free-text ATS column happened to name) — that column is
    frequently blank, "Custom", or just wrong, so it's a hint for ordering
    (stated providers go first, since they're more likely to hit), not a
    filter on what gets attempted."""
    candidates: list[tuple[str, str]] = []

    for provider, identifier in old_identifiers.get(company.lower(), []):
        candidates.append((provider, identifier))

    providers = stated_providers + [p for p in _GUESSABLE if p not in stated_providers] \
        if brute_force else stated_providers
    for provider in providers:
        if provider == "workday":
            continue  # unguessable without a real tenant/site; reuse-only above
        for variant in slug_variants(company):
            candidates.append((provider, variant))

    seen: set[tuple[str, str]] = set()
    for provider, identifier in candidates:
        key = (provider, identifier)
        if key in seen:
            continue
        seen.add(key)
        count = verify(provider, identifier)
        if count is not None:
            return company, provider, identifier, count

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument(
        "--retry-unresolved", action="store_true",
        help="Only re-attempt companies from the previous run's unresolved_companies.csv, "
        "keeping everything already resolved in data/companies.csv",
    )
    parser.add_argument(
        "--new-only", action="store_true",
        help="Leave every row already in data/companies.csv untouched (no re-verification, "
        "so a rate limit or flaky response can never drop an already-confirmed company), "
        "and only attempt companies from the sample sheet that aren't already verified.",
    )
    parser.add_argument(
        "--brute-force", action="store_true",
        help="Ignore the sample sheet's stated ATS hint as a filter (it's frequently blank, "
        "'Custom', or wrong) and try every guessable provider — greenhouse/lever/ashby/"
        "workable/smartrecruiters/teamtailor/bamboohr/recruitee — for every task company. "
        "Combine with --retry-unresolved to also pull in previously 'unsupported ATS' "
        "companies, since the stated platform no longer gates what gets attempted.",
    )
    args = parser.parse_args()

    rows = list(csv.DictReader(SAMPLE_CSV.open(encoding="utf-8")))
    old_identifiers = load_old_identifiers()
    print(f"Loaded {len(rows)} companies from sample sheet, "
          f"{len(old_identifiers)} previously-verified names to reuse as seeds.")

    # The sample sheet has duplicate company rows (repeated research passes with
    # differing/conflicting ATS guesses for the same name) — merge their free-text
    # ATS columns so every stated hint for a company is considered together.
    by_company: dict[str, tuple[str, str]] = {}  # lower name -> (display name, combined ats text)
    for row in rows:
        company = (row.get("Company") or "").strip()
        if not company:
            continue
        ats_raw = (row.get("ATS*") or row.get("ATS") or "").strip()
        key = company.lower()
        if key in by_company:
            disp, existing = by_company[key]
            by_company[key] = (disp, f"{existing} / {ats_raw}" if ats_raw else existing)
        else:
            by_company[key] = (company, ats_raw)

    kept_rows: list[dict] = []  # full original rows preserved verbatim (retry/new-only modes)
    retry_only: set[str] | None = None
    new_only_skip: set[str] = set()
    if args.retry_unresolved and UNRESOLVED_CSV.exists() and OUT_CSV.exists():
        retry_only = set()
        for r in csv.DictReader(UNRESOLVED_CSV.open(encoding="utf-8")):
            reason = r.get("Reason") or ""
            if "no live-verifiable identifier" in reason:
                retry_only.add(r["Company"])
            elif args.brute_force and reason.startswith("unsupported ATS"):
                retry_only.add(r["Company"])
        for r in csv.DictReader(OUT_CSV.open(encoding="utf-8")):
            kept_rows.append(r)  # preserved verbatim, incl. Research Status provenance
        print(f"Retry mode: keeping {len(kept_rows)} already-resolved, "
              f"retrying {len(retry_only)} unresolved"
              f"{' (brute-force: all guessable providers)' if args.brute_force else ' with wider slug variants'}.")
    elif args.new_only and OUT_CSV.exists():
        for r in csv.DictReader(OUT_CSV.open(encoding="utf-8")):
            kept_rows.append(r)
            new_only_skip.add(r["Company"].strip().lower())
        print(f"New-only mode: keeping {len(kept_rows)} already-verified companies "
              f"untouched, scanning sample sheet for names not among them.")

    tasks: list[tuple[str, list[str]]] = []
    skipped_unsupported: list[tuple[str, str]] = []
    for key, (company, ats_raw) in by_company.items():
        if retry_only is not None and company not in retry_only:
            continue
        if args.new_only and key in new_only_skip:
            continue
        providers = classify_providers(ats_raw)
        has_old = key in old_identifiers
        if not providers and not has_old and not args.brute_force:
            if retry_only is None:
                skipped_unsupported.append((company, ats_raw))
            continue
        tasks.append((company, providers))

    print(f"{len(tasks)} companies to attempt, {len(skipped_unsupported)} skipped "
          f"(no supported ATS adapter for their stated platform).")

    resolved: list[tuple[str, str, str, int]] = []
    unresolved: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(resolve_one, company, providers, old_identifiers, args.brute_force): company
            for company, providers in tasks
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            company = futures[future]
            result = future.result()
            if result is None:
                unresolved.append(company)
                print(f"[{done}/{len(tasks)}] UNRESOLVED  {company}")
            else:
                _, provider, identifier, count = result
                resolved.append(result)
                print(f"[{done}/{len(tasks)}] OK  {company}: {provider}/{identifier} ({count} jobs)")

    if retry_only is not None:
        # Preserve the unsupported-ATS rows from the previous unresolved report that
        # weren't part of this retry pass (brute-force mode pulls some of them in,
        # and those are already accounted for via `resolved`/`unresolved` above).
        for r in csv.DictReader(UNRESOLVED_CSV.open(encoding="utf-8")):
            reason = r.get("Reason") or ""
            if reason.startswith("unsupported ATS") and r["Company"] not in retry_only:
                skipped_unsupported.append((r["Company"], reason.replace("unsupported ATS: ", "")))

    resolved.sort(key=lambda r: r[0])
    unresolved.sort()

    fieldnames = [
        "Company", "Region", "Category", "Careers / Job Board URL",
        "Remote Policy (typical)", "Est. Junior AI/ML Salary Range (1-3 YOE)",
        "Notes", "Tier", "Why Target This Company", "Research Status",
        "ATS", "ATS Identifier",
    ]
    out_rows: list[dict] = list(kept_rows)  # untouched, verbatim (new-only mode)
    for company, provider, identifier, _count in resolved:
        out_rows.append({
            "Company": company,
            "Region": "",
            "Category": "",
            "Careers / Job Board URL": build_career_url(provider, identifier),
            "Remote Policy (typical)": "",
            "Est. Junior AI/ML Salary Range (1-3 YOE)": "",
            "Notes": "",
            "Tier": "",
            "Why Target This Company": "",
            "Research Status": "Verified live via resolve_new_companies.py"
            + (" --new-only" if args.new_only else "")
            + (" --retry-unresolved --brute-force" if args.retry_unresolved and args.brute_force
               else " --retry-unresolved" if args.retry_unresolved else ""),
            "ATS": provider,
            "ATS Identifier": identifier,
        })
    out_rows.sort(key=lambda r: r["Company"])

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in out_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    UNRESOLVED_CSV.parent.mkdir(parents=True, exist_ok=True)
    with UNRESOLVED_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Company", "Reason"])
        for company in unresolved:
            writer.writerow([company, "no live-verifiable identifier found"])
        for company, ats_raw in skipped_unsupported:
            writer.writerow([company, f"unsupported ATS: {ats_raw or '(blank)'}"])

    total_jobs = sum(c for *_r, c in resolved)
    print()
    print(f"Resolved {len(resolved)}/{len(tasks)} companies, {total_jobs} live jobs found across them.")
    print(f"Unresolved: {len(unresolved)} (see {UNRESOLVED_CSV})")
    print(f"Skipped (unsupported ATS): {len(skipped_unsupported)}")
    print(f"Wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
