# Companies we can't reliably get jobs from right now

Generated from 267 companies in data/companies.csv
(DB status cross-referenced from data/jobscraper.db where available).

## ATS detected but no adapter built for it (0)
These were manually identified (e.g. from ATS_TODO.md research) but we
don't have a working integration — most need a browser/paid API, so they
stay generic-HTML-only unless a new adapter gets built.


## ATS/adapter currently failing to fetch (0)
We have a real adapter for these, but the last run errored — bot-blocked,
stale identifier, or the site is temporarily down. Worth checking first,
these are often a quick CSV URL or identifier fix (see README).


## No ATS detected at all (0)
Falls back to generic HTML keyword parsing, which is lower-precision and
often finds 0 jobs (client-side-rendered pages, hard bot walls, or a
genuinely custom hiring platform). Run scripts/detect_ats_browser.py, or
check manually via ATS_TODO.md.

