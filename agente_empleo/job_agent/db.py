from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .models import Job, MatchResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT, company TEXT, location TEXT, url TEXT,
    posted_at TEXT, description TEXT,
    seniority TEXT, employment_type TEXT, remote INTEGER,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
    score REAL NOT NULL,
    reasons TEXT, gaps TEXT, advice TEXT,
    method TEXT, scored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
    status TEXT NOT NULL,
    applied_at TEXT,
    updated_at TEXT NOT NULL,
    next_action_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    at TEXT NOT NULL,
    status_from TEXT, status_to TEXT NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- jobs --
    def upsert_job(self, job: Job) -> bool:
        """Inserta o actualiza una oferta. Devuelve True si era nueva."""
        existing = self.conn.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
        self.conn.execute(
            """
            INSERT INTO jobs (id, source, title, company, location, url, posted_at, description,
                              seniority, employment_type, remote, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = COALESCE(NULLIF(excluded.title, ''), jobs.title),
                company = COALESCE(NULLIF(excluded.company, ''), jobs.company),
                location = COALESCE(NULLIF(excluded.location, ''), jobs.location),
                url = COALESCE(NULLIF(excluded.url, ''), jobs.url),
                posted_at = COALESCE(excluded.posted_at, jobs.posted_at),
                description = COALESCE(NULLIF(excluded.description, ''), jobs.description),
                seniority = COALESCE(excluded.seniority, jobs.seniority),
                employment_type = COALESCE(excluded.employment_type, jobs.employment_type),
                remote = COALESCE(excluded.remote, jobs.remote),
                fetched_at = excluded.fetched_at
            """,
            (
                job.id, job.source, job.title, job.company, job.location, job.url, job.posted_at,
                job.description, job.seniority, job.employment_type,
                None if job.remote is None else int(job.remote), now_iso(),
            ),
        )
        if existing is None:
            self.conn.execute(
                "INSERT INTO applications (job_id, status, updated_at) VALUES (?, 'descubierto', ?)",
                (job.id, now_iso()),
            )
        self.conn.commit()
        return existing is None

    def get_job(self, job_id: str) -> Optional[Job]:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def has_description(self, job_id: str) -> bool:
        row = self.conn.execute("SELECT description FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["description"])

    # ---------------------------------------------------------- matches --
    def upsert_match(self, match: MatchResult) -> None:
        self.conn.execute(
            """
            INSERT INTO matches (job_id, score, reasons, gaps, advice, method, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET score = excluded.score, reasons = excluded.reasons,
                gaps = excluded.gaps, advice = excluded.advice, method = excluded.method,
                scored_at = excluded.scored_at
            """,
            (
                match.job_id, match.score, json.dumps(match.reasons, ensure_ascii=False),
                json.dumps(match.gaps, ensure_ascii=False), match.advice, match.method, now_iso(),
            ),
        )
        self.conn.commit()

    def list_matches(
        self,
        *,
        min_score: float = 0.0,
        statuses: Optional[Iterable[str]] = None,
        limit: int = 50,
        company: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT j.*, m.score, m.reasons, m.gaps, m.advice, m.method,
                   a.status, a.applied_at, a.updated_at, a.next_action_at, a.notes
            FROM jobs j
            JOIN matches m ON m.job_id = j.id
            JOIN applications a ON a.job_id = j.id
            WHERE m.score >= ?
        """
        params: list = [min_score]
        if statuses:
            statuses = list(statuses)
            sql += f" AND a.status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        if company:
            sql += " AND LOWER(j.company) LIKE ?"
            params.append(f"%{company.lower()}%")
        sql += " ORDER BY m.score DESC, j.posted_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def get_match_row(self, job_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT j.*, m.score, m.reasons, m.gaps, m.advice, m.method,
                   a.status, a.applied_at, a.updated_at, a.next_action_at, a.notes
            FROM jobs j
            LEFT JOIN matches m ON m.job_id = j.id
            JOIN applications a ON a.job_id = j.id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()

    # ----------------------------------------------------- applications --
    def set_status(
        self,
        job_id: str,
        status: str,
        *,
        note: Optional[str] = None,
        next_action_at: Optional[str] = None,
    ) -> tuple[Optional[str], str]:
        row = self.conn.execute("SELECT status, applied_at, notes FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        previous = row["status"]
        applied_at = row["applied_at"]
        if status == "postulado" and not applied_at:
            applied_at = now_iso()
        notes = row["notes"]
        if note:
            stamp = datetime.now().strftime("%Y-%m-%d")
            notes = f"{notes}\n[{stamp}] {note}" if notes else f"[{stamp}] {note}"
        self.conn.execute(
            """
            UPDATE applications SET status = ?, applied_at = ?, updated_at = ?, next_action_at = ?, notes = ?
            WHERE job_id = ?
            """,
            (status, applied_at, now_iso(), next_action_at, notes, job_id),
        )
        self.conn.execute(
            "INSERT INTO events (job_id, at, status_from, status_to, note) VALUES (?, ?, ?, ?, ?)",
            (job_id, now_iso(), previous, status, note),
        )
        self.conn.commit()
        return previous, status

    def list_applications(self, statuses: Optional[Iterable[str]] = None) -> list[sqlite3.Row]:
        sql = """
            SELECT j.id, j.title, j.company, j.location, j.url, j.posted_at,
                   m.score, a.status, a.applied_at, a.updated_at, a.next_action_at, a.notes
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN matches m ON m.job_id = j.id
        """
        params: list = []
        if statuses:
            statuses = list(statuses)
            sql += f" WHERE a.status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        sql += " ORDER BY a.updated_at DESC"
        return self.conn.execute(sql, params).fetchall()

    def events_for(self, job_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM events WHERE job_id = ? ORDER BY at", (job_id,)).fetchall()

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM applications GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def scores_by_status(self) -> dict[str, list[float]]:
        rows = self.conn.execute(
            "SELECT a.status, m.score FROM applications a JOIN matches m ON m.job_id = a.job_id"
        ).fetchall()
        out: dict[str, list[float]] = {}
        for r in rows:
            out.setdefault(r["status"], []).append(r["score"])
        return out


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        source=row["source"],
        title=row["title"] or "",
        company=row["company"] or "",
        location=row["location"] or "",
        url=row["url"] or "",
        posted_at=row["posted_at"],
        description=row["description"] or "",
        seniority=row["seniority"],
        employment_type=row["employment_type"],
        remote=None if row["remote"] is None else bool(row["remote"]),
    )
