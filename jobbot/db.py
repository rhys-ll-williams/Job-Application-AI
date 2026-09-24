"""SQLite persistence: discovered jobs, application outcomes, remembered answers."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT NOT NULL,
    location      TEXT DEFAULT '',
    description   TEXT DEFAULT '',
    apply_kind    TEXT DEFAULT '',      -- easy_apply | external | unknown
    score         INTEGER,
    score_reason  TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new',
    -- new | rejected | queued | applied | failed | needs_human | dry_run
    status_note   TEXT DEFAULT '',
    found_at      TEXT NOT NULL,
    applied_at    TEXT,
    cv_path       TEXT DEFAULT '',
    cover_path    TEXT DEFAULT '',
    UNIQUE(source, external_id)
);
CREATE TABLE IF NOT EXISTS answers (
    key      TEXT PRIMARY KEY,          -- normalised question label
    question TEXT NOT NULL,
    answer   TEXT NOT NULL,
    source   TEXT NOT NULL,             -- profile | llm | user
    used     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------- jobs ---
    def add_job(self, *, source: str, external_id: str, url: str, title: str, company: str,
                location: str = "", description: str = "", apply_kind: str = "unknown") -> int | None:
        """Insert a job. Returns the new id, or None if we've already seen it."""
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO jobs(source, external_id, url, title, company, location, description,"
                    " apply_kind, found_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (source, external_id, url, title, company, location, description, apply_kind, _now()),
                )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def seen_same_role(self, title: str, company: str) -> bool:
        """Cross-site dedupe: same title+company already applied to or queued."""
        row = self.conn.execute(
            "SELECT 1 FROM jobs WHERE lower(title)=lower(?) AND lower(company)=lower(?)"
            " AND status IN ('applied','queued','dry_run') LIMIT 1",
            (title, company),
        ).fetchone()
        return row is not None

    def update_job(self, job_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.conn:
            self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def get_job(self, job_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def jobs_with_status(self, status: str, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY score DESC NULLS LAST, id LIMIT ?", (status, limit)
        ).fetchall()

    def applied_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status='applied' AND applied_at LIKE ?", (today + "%",)
        ).fetchone()
        return int(row["c"])

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    def mark_applied(self, job_id: int, status: str, note: str = "", cv: str = "", cover: str = "") -> None:
        self.update_job(job_id, status=status, status_note=note, cv_path=cv, cover_path=cover,
                        applied_at=_now() if status == "applied" else None)

    # ----------------------------------------------------------- answers ---
    def get_answer(self, key: str) -> str | None:
        row = self.conn.execute("SELECT answer FROM answers WHERE key=?", (key,)).fetchone()
        return row["answer"] if row else None

    def all_answers(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT key, question, answer, source FROM answers").fetchall()

    def save_answer(self, key: str, question: str, answer: str, source: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO answers(key, question, answer, source) VALUES (?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET answer=excluded.answer, source=excluded.source,"
                " used=used+1 WHERE answers.source != 'user'",
                (key, question, answer, source),
            )

    # ------------------------------------------------------------ events ---
    def log_event(self, job_id: int | None, kind: str, detail: Any = "") -> None:
        if not isinstance(detail, str):
            detail = json.dumps(detail, default=str)[:4000]
        with self.conn:
            self.conn.execute("INSERT INTO events(job_id, at, kind, detail) VALUES (?,?,?,?)",
                              (job_id, _now(), kind, detail))

    def close(self) -> None:
        with closing(self.conn):
            pass
