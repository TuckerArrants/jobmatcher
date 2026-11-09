"""SQLite storage: postings, classifier results, labels and runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from jobmatcher.models import Posting, parse_time

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    locations TEXT NOT NULL DEFAULT '[]',
    workplace TEXT,
    posted_at TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    text TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL DEFAULT '{}',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    prefilter TEXT            -- NULL = not yet filtered, 'kept', or the name of the rule that dropped it
);
CREATE TABLE IF NOT EXISTS results (
    posting_id TEXT NOT NULL,
    classifier TEXT NOT NULL,  -- e.g. "jev:jobs_v1#ab12cd34@jev-1.13.0"
    label TEXT,
    decision TEXT NOT NULL,    -- JSON: sub-scores, rule, sort score, confidence
    raw TEXT NOT NULL,         -- JSON: the classifier's raw answer
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (posting_id, classifier)
);
CREATE TABLE IF NOT EXISTS labels (
    posting_id TEXT PRIMARY KEY,
    label TEXT NOT NULL CHECK (label IN ('apply', 'consider', 'skip')),
    labeled_at TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    started_at TEXT NOT NULL,
    counts TEXT NOT NULL DEFAULT '{}',
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_postings_last_seen ON postings(last_seen);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the labeling server answers on its own thread; it serves one
        # request at a time, so access is never concurrent.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.db:
            yield self.db

    # ------------------------------------------------------------------ postings
    def upsert_postings(self, postings: list[Posting], seen_at: str | None = None) -> int:
        """Insert new postings and refresh last_seen on known ones. Returns how many were new."""
        seen_at = seen_at or now_iso()
        new = 0
        with self.tx() as db:
            for p in postings:
                exists = db.execute("SELECT 1 FROM postings WHERE id = ?", (p.id,)).fetchone()
                if exists:
                    db.execute("UPDATE postings SET last_seen = ? WHERE id = ?", (seen_at, p.id))
                    continue
                new += 1
                db.execute(
                    "INSERT INTO postings (id, source, company, title, url, locations, workplace,"
                    " posted_at, salary_min, salary_max, salary_currency, text, raw, first_seen,"
                    " last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        p.id, p.source, p.company, p.title, p.url, json.dumps(p.locations),
                        p.workplace, p.posted_at.isoformat() if p.posted_at else None,
                        p.salary_min, p.salary_max, p.salary_currency, p.text,
                        json.dumps(p.raw, default=str), seen_at, seen_at,
                    ),
                )
        return new

    @staticmethod
    def _to_posting(row: sqlite3.Row) -> Posting:
        return Posting(
            id=row["id"], source=row["source"], company=row["company"], title=row["title"],
            url=row["url"], text=row["text"], locations=json.loads(row["locations"]),
            workplace=row["workplace"], posted_at=parse_time(row["posted_at"]),
            salary_min=row["salary_min"], salary_max=row["salary_max"],
            salary_currency=row["salary_currency"], raw=json.loads(row["raw"]),
        )

    def postings(self, where: str = "1=1", params: tuple = ()) -> list[Posting]:
        rows = self.db.execute(f"SELECT * FROM postings WHERE {where} ORDER BY first_seen, id", params)
        return [self._to_posting(r) for r in rows]

    def get(self, posting_id: str) -> Posting | None:
        found = self.postings("id = ?", (posting_id,))
        return found[0] if found else None

    def set_prefilter(self, outcomes: dict[str, str]) -> None:
        with self.tx() as db:
            db.executemany(
                "UPDATE postings SET prefilter = ? WHERE id = ?",
                [(v, k) for k, v in outcomes.items()],
            )

    def count(self, where: str = "1=1", params: tuple = ()) -> int:
        return self.db.execute(f"SELECT COUNT(*) FROM postings WHERE {where}", params).fetchone()[0]

    # ------------------------------------------------------------------ results
    def has_result(self, posting_id: str, classifier: str) -> bool:
        return bool(self.db.execute(
            "SELECT 1 FROM results WHERE posting_id = ? AND classifier = ?", (posting_id, classifier)
        ).fetchone())

    def save_result(
        self, posting_id: str, classifier: str, label: str | None, decision: dict[str, Any],
        raw: Any, cost_usd: float,
    ) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?, ?)",
                (posting_id, classifier, label, json.dumps(decision), json.dumps(raw),
                 cost_usd, now_iso()),
            )

    def results(self, classifier: str) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM results WHERE classifier = ?", (classifier,)))

    # ------------------------------------------------------------------ labels
    def set_label(self, posting_id: str, label: str, note: str | None = None) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO labels VALUES (?, ?, ?, ?)",
                (posting_id, label, now_iso(), note),
            )

    def labels(self) -> dict[str, str]:
        return {r["posting_id"]: r["label"] for r in self.db.execute("SELECT * FROM labels")}

    # ------------------------------------------------------------------ runs
    def runs(self, command: str, since: str | None = None) -> list[sqlite3.Row]:
        """Runs of one command, oldest first, optionally only those after ``since``."""
        if since:
            q = "SELECT * FROM runs WHERE command = ? AND started_at > ? ORDER BY id"
            return list(self.db.execute(q, (command, since)))
        return list(self.db.execute("SELECT * FROM runs WHERE command = ? ORDER BY id", (command,)))

    def latest_seen(self) -> str | None:
        return self.db.execute("SELECT MAX(last_seen) FROM postings").fetchone()[0]

    def record_run(self, command: str, counts: dict[str, Any], cost_usd: float = 0.0) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO runs (command, started_at, counts, cost_usd) VALUES (?, ?, ?, ?)",
                (command, now_iso(), json.dumps(counts), cost_usd),
            )
