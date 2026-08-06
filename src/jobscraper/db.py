"""SQLite persistence layer. No cloud database — the .db file itself is the
durable state that survives across ephemeral GitHub Actions runs (committed
back to the repo by the workflow after each run).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from jobscraper.models import Company, Job, utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    career_page TEXT,
    region TEXT,
    priority TEXT,
    notes TEXT,
    ats_provider TEXT,
    ats_identifier TEXT,
    last_checked_at TEXT,
    last_status TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_hash TEXT UNIQUE NOT NULL,
    company_id INTEGER REFERENCES companies(id),
    company_name TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    remote INTEGER DEFAULT 0,
    apply_url TEXT NOT NULL,
    source TEXT NOT NULL,
    ats_platform TEXT,
    posted_date TEXT,
    company_priority TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    rank_score REAL,
    rank_stars INTEGER,
    rank_reason TEXT,
    jd_keywords TEXT
);

CREATE TABLE IF NOT EXISTS applied_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_hash TEXT NOT NULL REFERENCES jobs(job_hash),
    applied_at TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    total_jobs_found INTEGER,
    new_jobs_found INTEGER,
    companies_checked INTEGER,
    companies_failed INTEGER,
    sources_checked INTEGER,
    sources_failed INTEGER,
    report_html_path TEXT,
    report_md_path TEXT,
    email_sent INTEGER
);
"""


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate_daily_reports_columns()
        self._migrate_jobs_columns()
        self.conn.commit()

    def _migrate_daily_reports_columns(self) -> None:
        """daily_reports has gone through two shapes: originally
        companies_checked/companies_failed (per-company pass only), briefly
        renamed to sources_checked/sources_failed (aggregator-only pass),
        now both passes run and need their own columns. Handles a DB
        currently in either older shape."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(daily_reports)")}
        if "companies_checked" in cols and "sources_checked" not in cols:
            # Still in the original shape: this DB predates both renames.
            # Copy into sources_checked/failed too so both concepts exist;
            # historical values won't reflect a real aggregator-only count,
            # but that's unavoidable for runs that predate the split.
            self.conn.execute("ALTER TABLE daily_reports ADD COLUMN sources_checked INTEGER")
            self.conn.execute("ALTER TABLE daily_reports ADD COLUMN sources_failed INTEGER")
        elif "companies_checked" not in cols and "sources_checked" in cols:
            # Went through the aggregator-only rename; add companies_* back.
            self.conn.execute("ALTER TABLE daily_reports ADD COLUMN companies_checked INTEGER")
            self.conn.execute("ALTER TABLE daily_reports ADD COLUMN companies_failed INTEGER")

    def _migrate_jobs_columns(self) -> None:
        """Adds jd_keywords to a jobs table created before Gemini-based
        skill extraction existed."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(jobs)")}
        if "jd_keywords" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN jd_keywords TEXT")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- companies -----------------------------------------------------

    def upsert_company(self, company: Company) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO companies (name, career_page, region, priority, notes)
            VALUES (:name, :career_page, :region, :priority, :notes)
            ON CONFLICT(name) DO UPDATE SET
                career_page = excluded.career_page,
                region = excluded.region,
                priority = excluded.priority,
                notes = excluded.notes
            """,
            {
                "name": company.name,
                "career_page": company.career_page,
                "region": company.region,
                "priority": company.priority,
                "notes": company.notes,
            },
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM companies WHERE name = ?", (company.name,)
        ).fetchone()
        return row["id"]

    def record_company_check(
        self,
        company_id: int,
        ats_provider: str | None,
        ats_identifier: str | None,
        status: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE companies
            SET ats_provider = ?, ats_identifier = ?,
                last_checked_at = ?, last_status = ?
            WHERE id = ?
            """,
            (ats_provider, ats_identifier, utcnow_iso(), status, company_id),
        )
        self.conn.commit()

    # -- jobs ------------------------------------------------------------

    def job_exists(self, job_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM jobs WHERE job_hash = ?", (job_hash,)
        ).fetchone()
        return row is not None

    def is_applied(self, job_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM applied_jobs WHERE job_hash = ?", (job_hash,)
        ).fetchone()
        return row is not None

    def all_applied_hashes(self) -> set[str]:
        rows = self.conn.execute("SELECT job_hash FROM applied_jobs").fetchall()
        return {r["job_hash"] for r in rows}

    def all_known_hashes(self) -> set[str]:
        rows = self.conn.execute("SELECT job_hash FROM jobs").fetchall()
        return {r["job_hash"] for r in rows}

    def upsert_job(self, job: Job) -> None:
        now = utcnow_iso()
        existing = self.conn.execute(
            "SELECT first_seen_at FROM jobs WHERE job_hash = ?", (job.job_hash,)
        ).fetchone()
        first_seen_at = existing["first_seen_at"] if existing else now

        self.conn.execute(
            """
            INSERT INTO jobs (
                job_hash, company_id, company_name, title, location, remote,
                apply_url, source, ats_platform, posted_date, company_priority,
                first_seen_at, last_seen_at, rank_score, rank_stars, rank_reason,
                jd_keywords
            ) VALUES (
                :job_hash, :company_id, :company_name, :title, :location, :remote,
                :apply_url, :source, :ats_platform, :posted_date, :company_priority,
                :first_seen_at, :last_seen_at, :rank_score, :rank_stars, :rank_reason,
                :jd_keywords
            )
            ON CONFLICT(job_hash) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                rank_score = excluded.rank_score,
                rank_stars = excluded.rank_stars,
                rank_reason = excluded.rank_reason,
                apply_url = excluded.apply_url,
                posted_date = excluded.posted_date,
                jd_keywords = excluded.jd_keywords
            """,
            {
                "job_hash": job.job_hash,
                "company_id": job.company_id,
                "company_name": job.company_name,
                "title": job.title,
                "location": job.location,
                "remote": int(job.remote),
                "apply_url": job.apply_url,
                "source": job.source,
                "ats_platform": job.ats_platform,
                "posted_date": job.posted_date,
                "company_priority": job.company_priority,
                "first_seen_at": first_seen_at,
                "last_seen_at": now,
                "rank_score": job.rank_score,
                "rank_stars": job.rank_stars,
                "rank_reason": job.rank_reason,
                "jd_keywords": json.dumps(job.jd_keywords) if job.jd_keywords else None,
            },
        )
        self.conn.commit()

    def mark_applied(self, job_hash: str, notes: str = "") -> None:
        self.conn.execute(
            "INSERT INTO applied_jobs (job_hash, applied_at, notes) VALUES (?, ?, ?)",
            (job_hash, utcnow_iso(), notes),
        )
        self.conn.commit()

    # -- pipeline state ----------------------------------------------------

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM pipeline_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO pipeline_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.conn.commit()

    # -- daily reports -----------------------------------------------------

    def record_daily_report(
        self,
        run_date: str,
        total_jobs_found: int,
        new_jobs_found: int,
        companies_checked: int,
        companies_failed: int,
        sources_checked: int,
        sources_failed: int,
        report_html_path: str,
        report_md_path: str,
        email_sent: bool,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO daily_reports (
                run_date, total_jobs_found, new_jobs_found,
                companies_checked, companies_failed,
                sources_checked, sources_failed,
                report_html_path, report_md_path, email_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date,
                total_jobs_found,
                new_jobs_found,
                companies_checked,
                companies_failed,
                sources_checked,
                sources_failed,
                report_html_path,
                report_md_path,
                int(email_sent),
            ),
        )
        self.conn.commit()
