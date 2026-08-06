# 🚀 AI Job Discovery Automation

<p align="center">
  <img src="https://img.shields.io/badge/Companies_Tracked-268-2563eb?style=for-the-badge" alt="Companies Tracked"/>
  <img src="https://img.shields.io/badge/ATS_Coverage-100%25_live--verified-16a34a?style=for-the-badge" alt="ATS Coverage"/>
  <img src="https://img.shields.io/badge/Global_Sources-4-f97316?style=for-the-badge" alt="Global Sources"/>
  <img src="https://img.shields.io/badge/Tests-50_passing-22c55e?style=for-the-badge" alt="Tests Passing"/>
  <img src="https://img.shields.io/badge/Cost-%240%2Fmonth-16a34a?style=for-the-badge" alt="Cost"/>
</p>

<p align="center">
  <i>A daily, fully-free pipeline that finds high-quality AI Engineering job<br/>
  postings from ~268 live-verified target companies plus trusted public job boards, dedupes<br/>
  against jobs already seen, ranks them for relevance, and emails you a report<br/>
  every morning at 8:00 AM IST.</i>
</p>

---

## 📊 Snapshot (companies list as of 2026-08-06; job counts as of the last full run: 2026-08-03)

Since the `Rebuild company list from live-verified ATS identifiers` change,
`data/companies.csv` only ever holds companies whose (ATS provider, ATS
identifier) was confirmed with a real HTTP call to that provider's public
API — there's no generic-HTML-fallback tier anymore. Unverifiable companies
live in `reports/unresolved_companies.csv` instead, pending manual research.

<table>
<tr>
  <td align="center">🏢<br/><b>268</b><br/>companies tracked</td>
  <td align="center">✅<br/><b>100%</b><br/>live-verified ATS (no generic fallback)</td>
  <td align="center">🕵️<br/><b>235</b><br/>candidates still pending (no supported ATS found)</td>
  <td align="center">⚠️<br/><b>0</b><br/>currently failing to fetch</td>
</tr>
<tr>
  <td align="center">📬<br/><b>78</b><br/>new jobs found (last run, 123-company list)</td>
  <td align="center">📦<br/><b>115</b><br/>total relevant jobs (last run, 123-company list)</td>
  <td align="center">⭐<br/><b>19</b><br/>top picks emailed (last run)</td>
  <td align="center">📧<br/><b>✅ sent</b><br/>daily digest</td>
</tr>
</table>

*Job-count figures above predate the 123→268 company expansion — they're
from the last actual pipeline run, not the current list. Re-run
`python scripts/run_daily.py` to refresh them against all 268.*

### 🎯 ATS provider coverage (of 268 tracked companies, all live-verified)

| Provider | Companies matched | |
|---|---:|---|
| 🟪 Ashby | 113 | ████████████████████ |
| 🟩 Greenhouse | 83 | ███████████████ |
| 🟦 Workday | 40 | ███████ |
| 🟧 Lever | 19 | ███ |
| 🟨 SmartRecruiters | 6 | █ |
| 🟥 Oracle Recruiting Cloud | 2 | █ |
| ⬛ Workable | 2 | █ |
| 🟫 BambooHR | 2 | █ |
| ⬜ TeamTailor | 1 | █ |
| | | |
| **Total confirmed** | **268 / 268 (100%)** | |

Run `python scripts/unsupported_companies_report.py` any time for a fresh,
company-by-company breakdown of exactly what's covered and what isn't. To
verify more companies, add candidates (Company, ATS free-text guess) to
`data/companies_sample - Sheet1.csv` and run
`python scripts/resolve_new_companies.py --new-only` — it live-verifies only
the names not already in `data/companies.csv` and never touches or
re-checks companies already confirmed. `--retry-unresolved --brute-force`
retries everything in `reports/unresolved_companies.csv` against every
guessable provider (Greenhouse/Lever/Ashby/Workable/SmartRecruiters/
TeamTailor/BambooHR/Recruitee), ignoring the sheet's stated ATS hint — useful
since that hint is frequently blank, wrong, or just "Custom". The remaining
235 pending candidates are companies confirmed this way to be on a custom/
proprietary hiring portal or an ATS this project doesn't have an adapter for
(Zoho Recruit, Keka, Darwinbox, RippleHire, Breezy HR, JazzHR, Gem, Eightfold).

---

## What it does, every day

1. Checks every company in `data/companies.csv` (a target list, not a
   scrape-everything list) — auto-detecting Greenhouse, Lever, Ashby,
   Workday, SmartRecruiters, Teamtailor, BambooHR, Workable, Recruitee, and
   Oracle Recruiting Cloud job boards and calling each one's public API
   directly; falls back to a heuristic HTML parser for everything else.
2. Pulls jobs from RemoteOK's public API, We Work Remotely's RSS feed,
   Himalayas' public search API, and Jobicy's public API (Data Science &
   Analytics industry filter).
3. Filters down to what's actually worth sending to Gemini, in order:
   drops obviously-wrong functions (marketing/sales/HR/... and internships,
   see `config/config.yaml`), then hard-drops anything requiring more than
   `profile.max_years_experience_hard_limit` years, then hard-drops
   anything that's neither India/worldwide remote nor on-site/hybrid in a
   configured hub city (Gurgaon/Noida by default).
4. Dedupes against everything seen before, and against jobs you've marked as
   applied — nothing is ever recommended twice.
5. Sends each brand-new job's JD to Gemini (batched) to extract the actual
   skills/technologies it asks for, ranked by importance — see
   `gemini_extractor.py`. Falls back to a plain keyword scan if
   `GEMINI_API_KEY` isn't set or a request fails.
6. Ranks every job 1-5 stars based on title match, skill overlap (your
   `profile.preferred_keywords` matched against the extracted JD skills,
   weighted by their importance), remote/country fit, and
   years-of-experience fit. Any job with zero skill overlap is dropped —
   that's the sole relevance gate; title is never used to include or
   exclude a job.
7. Writes an HTML + Markdown report to `reports/`, and emails every new job
   that passed the filters above, ranked by score — jobs at or above
   `email.min_score_for_recommendation` land in "Top Recommendations",
   everything else in a collapsible "Other jobs worth looking at" section,
   annotated with why it didn't clear the bar. No count caps on either
   section.

## Project layout

```
config/config.yaml          All tunable behavior — roles, keywords, countries, schedule, email
data/companies.csv           Your target companies (Company, Region, Careers URL, Tier, Notes, ...)
data/jobscraper.db           SQLite state: companies, jobs, applied_jobs, daily_reports
src/jobscraper/
  ats/                       One parser per ATS provider + detect.py (sniffs career pages) + generic_html.py fallback
  sources/                   RemoteOK + We Work Remotely + Himalayas + Jobicy
  filtering.py                Categorical exclude (function/internship), not a relevance filter
  gemini_extractor.py         Gemini JD -> ranked skill list extraction (batched)
  dedupe.py                  Hashing + previously-seen/applied checks
  ranking.py                 1-5 star relevance scoring + skill matching
  report/                    Markdown + HTML (Jinja2) report rendering
  email_sender.py            Gmail SMTP sending
  pipeline.py                Orchestrates one full daily run
scripts/
  run_daily.py               Main entrypoint (local + GitHub Actions)
  mark_applied.py            CLI to mark a job as applied
  detect_ats.py              Pre-detects each company's ATS and caches it into companies.csv
  detect_ats_browser.py      Thorough browser-based re-check (JS-rendered/linked ATS integrations)
  unsupported_companies_report.py  Snapshot of companies with no working ATS integration
tests/                       pytest suite with fixtures for every parser
.github/workflows/daily.yml  Runs the pipeline every day at 08:00 IST
```

## Setup

1. **Install dependencies** (Python 3.12):
   ```
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   pip install -r requirements.txt
   ```

2. **Gmail App Password** (for sending the report):
   - Enable 2-Step Verification on the sending Gmail account.
   - Google Account → Security → App passwords → generate one for "Mail".
   - Copy `.env.example` to `.env` and fill in `EMAIL_SENDER` /
     `EMAIL_APP_PASSWORD`. `.env` is gitignored — never commit it.

2b. **Gemini API key** (for JD skill extraction):
   - Get a key at https://aistudio.google.com/apikey.
   - Add it to `.env` as `GEMINI_API_KEY`. If left unset, the pipeline logs
     it and falls back to a plain keyword scan instead of failing — see
     `gemini.enabled` in `config/config.yaml`.

3. **Review `config/config.yaml`**: preferred countries, remote preference,
   your actual skill set (`profile.preferred_keywords` — this is what JD
   skills are matched against), experience ceiling, onsite hub cities, and
   the email recipient all live here.

4. **Run it**:
   ```
   python scripts/run_daily.py --limit 10 --no-email   # smoke test, no email
   python scripts/run_daily.py                          # full run, sends email
   ```

5. **Mark a job as applied** so it's never recommended again:
   ```
   python scripts/mark_applied.py --company "Acme Inc" --title "AI Engineer" --location "Remote, India"
   ```

5b. **Pre-detect ATS providers** (optional, but recommended after adding/changing companies):
   ```
   python scripts/detect_ats.py
   ```
   This fetches every company's career page once, sniffs it for a known ATS
   signature, and writes the result into two new `data/companies.csv`
   columns: `ATS` and `ATS Identifier`. The daily run then calls that ATS's
   API directly for those companies instead of fetching the company's own
   career page — the exact request most bot-protected sites block. Re-run
   this occasionally (e.g. monthly) to catch companies that migrate to a
   different ATS; the daily run trusts whatever is cached here until then.

5c. **Thorough browser-based re-check** (optional, slower, catches more):
   ```
   pip install playwright && python -m playwright install chromium
   python scripts/detect_ats_browser.py
   ```
   `detect_ats.py` only reads the raw HTTP response — it misses ATS
   integrations that only render after JS runs, or that live on a linked
   "view jobs" page rather than the landing page in the CSV. This script
   renders each company's page in a real (headless) browser, checks the
   fully-rendered DOM, and — if nothing matches — follows the most likely
   "view jobs" link or button one hop and checks again. Every match is
   written to the CSV immediately, so it's safe to interrupt (Ctrl+C) at
   any point without losing progress; re-running picks up where you left
   off (it skips companies that already have an ATS set, unless you pass
   `--all`). It's slower than `detect_ats.py` (one real page load per
   company) and not run automatically — use it occasionally, the same way.

5d. **See what's not covered yet**:
   ```
   python scripts/unsupported_companies_report.py
   ```
   Prints (and writes `UNSUPPORTED_COMPANIES.md`) a categorized snapshot of
   every company with no working ATS integration — split into "ATS known
   but no adapter built", "adapter currently erroring", and "no ATS
   detected at all" — cross-referenced against the DB's real last-fetch
   status where available.

6. **Run tests**:
   ```
   pytest -q
   ```

## Deploying the daily GitHub Actions run

1. Push this repo to GitHub.
2. In the repo's Settings → Secrets and variables → Actions, add:
   - `EMAIL_SENDER`
   - `EMAIL_APP_PASSWORD`
   - `GEMINI_API_KEY`
3. The workflow (`.github/workflows/daily.yml`) runs every day at 08:00 IST
   (`30 2 * * *` UTC) and can also be triggered manually from the Actions tab
   (`workflow_dispatch`).
4. After each run, the workflow commits `data/jobscraper.db` and `reports/`
   back to the repo — this is how state persists across GitHub Actions' own
   ephemeral runners without needing a cloud database.

## Design decisions

- **ATS detection sniffs the fetched page, not the CSV URL.** Almost none of
  the 215 target companies link directly to a `greenhouse.io`/`lever.co`
  URL — they link to their own domain (e.g. `careers.adobe.com`), which
  embeds or redirects to the actual ATS. `ats/detect.py` regex-scans the
  fetched HTML and the final resolved URL for ATS signatures, then hands off
  to that provider's dedicated adapter, which calls the ATS's public JSON
  API directly (fast, structured, no HTML scraping needed for those
  providers).
- **Static HTML fetching by default, browser-based re-check on demand.**
  Some career sites render their job list entirely client-side via
  JavaScript, or only reveal the ATS after following a "view jobs" link —
  `detect_ats.py`'s single static fetch misses those. `detect_ats_browser.py`
  covers that gap using a real headless browser (Playwright), but only as an
  occasional manual re-check — running a browser for 215 companies as part
  of the free daily automation isn't practical, so the daily run keeps using
  whatever ATS is already cached in the CSV.
- **RemoteOK + We Work Remotely + Himalayas + Jobicy for Source B, for now.**
  Wellfound and YC Jobs require login or defeat non-browser scraping with
  anti-bot measures (Wellfound sits behind DataDome, same as LinkedIn/Google
  Jobs — bypassing it means ToS-violating stealth-browser scraping, which
  this project won't do); rather than build something brittle, `sources/` is
  a small plugin interface so a new source is a single new file plus one
  line in `pipeline.py`. Also investigated and ruled out as global sources:
  Remotive (documented API, but every query param — `category`, `search`,
  `tag` — is currently silently ignored, always returning the same fixed
  33-job window), NoDesk and Working Nomads (real feeds, but tiny/unfiltered
  — 10-28 fixed recent items across all categories, near-zero AI-relevant
  hits). Jobicy's `industry=data-science` filter, by contrast, genuinely
  works server-side and returns real, relevant results. Also investigated
  and ruled out as ATS adapters: iCIMS
  (AWS WAF JS challenge on every tenant), SAP SuccessFactors (job data only
  loads via authenticated AJAX), Eightfold.ai (API requires a partner key),
  Zoho Recruit (pure client-side rendering), Phenom People (OAuth token
  gated behind their sales team), Jobvite (per-customer opt-in API, public
  career pages are JS-app shells). Workable, Recruitee, and Oracle
  Recruiting Cloud, by contrast, all turned out to have genuine free public
  APIs and got dedicated adapters (`ats/workable.py`, `ats/recruitee.py`,
  `ats/oracle_recruiting.py`) — the difference is simply whether the
  platform serves candidate-facing job data from its own servers or only
  renders it client-side/behind a gate.
- **SQLite file committed back to the repo** is the persistence layer.
  There's no cloud database per the requirements, and GitHub Actions
  runners are ephemeral, so the workflow's last step commits the updated
  `.db` file and the day's reports back to `main`.
- **One bad company never aborts the run.** Every fetch — company career
  page, ATS API call, RSS/API source — is wrapped so exceptions are logged
  and turned into an empty result; `pipeline.py` tracks
  `companies_failed` for visibility without stopping the other 214.
- **Relevance is skill-based, not title-based.** A job's title is never used
  to include or exclude it (`roles.include` is only search-time discovery
  keywords + a small title-match scoring bonus) — the only relevance gate is
  whether `ranking.has_stack_overlap` is true, i.e. whether the JD's
  extracted skills overlap with `profile.preferred_keywords` at all.
- **Experience and location are hard filters, applied *before* Gemini runs**
  (`pipeline._within_experience_cap`, `pipeline._location_qualifies`), not
  scoring inputs — this is deliberate: it keeps the (rate-limited, costs
  money) Gemini extraction step from ever running on a job that couldn't
  qualify anyway. A job over `profile.max_years_experience_hard_limit`
  (default 5) or outside India/worldwide-remote and outside
  `profile.onsite_hub_cities` is dropped outright, before its JD is ever
  sent anywhere.
- **JD skills are extracted by Gemini, ranked by importance.**
  `gemini_extractor.py` batches new jobs' JD text into one request per
  batch and asks for each job's skills/technologies most → least emphasized
  in the text. `ranking._match_skills` matches that ranked list against
  `profile.preferred_keywords`, weighting a match on the JD's #1 skill more
  than one buried at #10 (`ranking._importance_weight`). If extraction is
  disabled, the API key is missing, or a request fails, ranking silently
  falls back to a plain substring scan of the job text — a Gemini outage
  never breaks the pipeline.
- **Ranking is a transparent weighted score**, not a black box: title match
  (0-40), skill overlap (0-25), remote fit (0-15), country fit (0-10),
  YOE fit (0-10) → mapped to 1-5 stars. YOE fit extracts the minimum years
  of experience mentioned in the job text (the lower bound for a range like
  "3-5 years") and compares it against `profile.max_years_experience`
  (default 3, distinct from the hard `_hard_limit` filter above): at or
  under that preferred threshold scores favorably (10/10), over it decays
  on a gradient (2 points per year over, floored at 1) rather than dropping
  straight to a flat low score, and no YOE mentioned at all is neutral
  (5/10) — it's not penalized just because it wasn't stated. The
  `rank_reason` string surfaces which factors fired, and the "why it
  matches" field in the email is built directly from it.
- **Every job that passes every filter is reported, ranked by score, with
  no count cap.** `pipeline._qualifies_for_recommendation` is just a score
  threshold (`email.min_score_for_recommendation`, default 70) — since
  experience, location, and skill overlap were already hard-filtered
  earlier in the pipeline, by the time a job is scored it has already
  qualified on everything except how well it scores. Jobs below the
  threshold land in "Other jobs worth looking at" instead of the main list,
  each annotated with the score gap (`pipeline._not_recommended_reason`)
  rather than silently dropped.

## Future extensibility (not implemented yet, designed for)

The codebase is intentionally structured so these can be added without
touching the rest of the pipeline:

- **Resume matching**: a `resume.py` module could embed/keyword-match a
  resume file against `job.description` and feed an extra score into
  `ranking.score_job`.
- **AI-based ranking**: swap the scoring function in `ranking.py` for an
  LLM-based scorer behind the same `score_job(job, profile, roles)` signature.
- **Cover letter / interview question generation**: new modules under a
  `generation/` package, using the full `job.description` already stored in
  SQLite.
- **Slack / Discord / Telegram notifications**: new modules parallel to
  `email_sender.py`, each implementing a `send(report_data) -> bool` style
  interface, wired into `pipeline.py` next to the existing email step.

## Known limitations

- Generic HTML fallback parsing is heuristic (keyword-matches anchor text)
  and has lower precision than the dedicated ATS adapters — expected, given
  it covers arbitrary custom career pages.
- Workday detection/parsing targets the public CXS search endpoint most
  Workday career sites use; some tenants customize this enough that the
  adapter returns nothing for them (logged, not fatal).
- YC Jobs and Wellfound are not yet implemented as sources (see above).
- The KPI snapshot at the top of this README is a point-in-time figure, not
  a live dashboard — re-run `scripts/unsupported_companies_report.py` (and
  update this section) whenever you want current numbers.
