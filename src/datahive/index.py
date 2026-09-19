"""Local SQLite index at samples/.datahive/index.db.

This is the ONLY status tracker for episodes -- both the CLI and the GUI
backend read/write through this module, never a separate reimplementation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from datahive.config import now_iso, to_utc_iso
from datahive.paths import index_db_path, iter_all_episode_ids

STATUSES = ("recorded", "validated", "uploaded", "upload_failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    episode_id     TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    trial_id       TEXT,
    status         TEXT NOT NULL,
    content_hash   TEXT,
    uploaded_hash  TEXT,
    last_error     TEXT,
    first_seen_at  TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_synced_at TEXT,
    created_at     TEXT,
    n_steps        INTEGER,
    duration_s     REAL
);
CREATE INDEX IF NOT EXISTS episodes_status_idx ON episodes(status);
"""


@dataclass
class EpisodeRecord:
    episode_id: str
    session_id: str
    trial_id: str | None
    status: str
    content_hash: str | None
    uploaded_hash: str | None
    last_error: str | None
    first_seen_at: str
    updated_at: str
    last_synced_at: str | None
    created_at: str | None = None
    n_steps: int | None = None
    duration_s: float | None = None

    @property
    def effective_status(self) -> str:
        """`uploaded` degrades to reporting a pending re-upload if the
        on-disk content has changed since the last successful upload."""
        if self.status == "uploaded" and self.content_hash != self.uploaded_hash:
            return "uploaded (modified)"
        return self.status

    @property
    def needs_upload(self) -> bool:
        if self.status == "upload_failed":
            return True
        if self.status == "uploaded" and self.content_hash != self.uploaded_hash:
            return True
        return self.status == "validated"


class Index:
    def __init__(self, samples_root: Path):
        self.samples_root = Path(samples_root)
        self.db_path = index_db_path(self.samples_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        """Migration for index.db files created before these columns existed."""
        cols = [row[1] for row in self.conn.execute("PRAGMA table_info(episodes)").fetchall()]
        for name, sql_type in (("created_at", "TEXT"), ("n_steps", "INTEGER"), ("duration_s", "REAL")):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE episodes ADD COLUMN {name} {sql_type}")

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> EpisodeRecord:
        return EpisodeRecord(**{k: row[k] for k in row.keys()})

    def get(self, episode_id: str) -> EpisodeRecord | None:
        cur = self.conn.execute("SELECT * FROM episodes WHERE episode_id = ?", (episode_id,))
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def all(self, *, status: str | None = None, query: str | None = None) -> list[EpisodeRecord]:
        sql = "SELECT * FROM episodes"
        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            clauses.append("(episode_id LIKE ? OR session_id LIKE ? OR trial_id LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        sql += " ORDER BY (created_at IS NULL), created_at DESC, updated_at DESC"
        cur = self.conn.execute(sql, params)
        return [self._row_to_record(r) for r in cur.fetchall()]

    def _upsert_new(
        self,
        episode_id: str,
        session_id: str,
        trial_id: str | None,
        created_at: str | None,
        n_steps: int | None,
        duration_s: float | None,
    ) -> None:
        now = now_iso()
        with self.conn:
            self.conn.execute(
                """INSERT INTO episodes
                   (episode_id, session_id, trial_id, status, content_hash,
                    uploaded_hash, last_error, first_seen_at, updated_at, last_synced_at,
                    created_at, n_steps, duration_s)
                   VALUES (?, ?, ?, 'recorded', NULL, NULL, NULL, ?, ?, NULL, ?, ?, ?)
                   ON CONFLICT(episode_id) DO NOTHING""",
                (episode_id, session_id, trial_id, now, now, created_at, n_steps, duration_s),
            )

    def scan(self) -> list[str]:
        """Scan samples/ for episode folders not yet in the index; add them
        as 'recorded'. Returns newly-added episode_ids. Also backfills
        created_at/n_steps/duration_s for rows added before those columns
        existed."""
        from datahive.episode import episode_stats, read_header

        def _h5_path(session_id: str, episode_id: str) -> Path:
            return self.samples_root / session_id / "episodes" / f"{episode_id}.h5"

        def _read_header_safe(session_id: str, episode_id: str):
            try:
                return read_header(_h5_path(session_id, episode_id))
            except Exception:
                return None

        def _stats_safe(session_id: str, episode_id: str) -> dict:
            try:
                return episode_stats(_h5_path(session_id, episode_id))
            except Exception:
                return {"n_steps": None, "duration_s": None}

        newly_added: list[str] = []
        for session_id, episode_id in iter_all_episode_ids(self.samples_root):
            if self.get(episode_id) is not None:
                continue
            header = _read_header_safe(session_id, episode_id)
            trial_id = header.trial_id if header else None
            created_at = to_utc_iso(header.created_at) if header else None
            stats = _stats_safe(session_id, episode_id)
            self._upsert_new(
                episode_id, session_id, trial_id, created_at, stats["n_steps"], stats["duration_s"]
            )
            newly_added.append(episode_id)

        for rec in self.all():
            if rec.created_at and rec.n_steps is not None:
                continue
            header = _read_header_safe(rec.session_id, rec.episode_id)
            created_at = to_utc_iso(header.created_at) if header else rec.created_at
            stats = _stats_safe(rec.session_id, rec.episode_id)
            with self.conn:
                self.conn.execute(
                    "UPDATE episodes SET created_at = ?, n_steps = ?, duration_s = ? WHERE episode_id = ?",
                    (created_at, stats["n_steps"], stats["duration_s"], rec.episode_id),
                )

        return newly_added

    def set_status(self, episode_id: str, status: str, *, error: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        with self.conn:
            self.conn.execute(
                "UPDATE episodes SET status = ?, last_error = ?, updated_at = ? WHERE episode_id = ?",
                (status, error, now_iso(), episode_id),
            )

    def refresh_hash(self, episode_id: str) -> str:
        from datahive.episode import content_hash

        rec = self.get(episode_id)
        if rec is None:
            raise KeyError(episode_id)
        h = content_hash(self.samples_root, episode_id, rec.session_id)
        with self.conn:
            self.conn.execute(
                "UPDATE episodes SET content_hash = ?, updated_at = ? WHERE episode_id = ?",
                (h, now_iso(), episode_id),
            )
        return h

    def mark_uploaded(self, episode_id: str, content_hash: str, when: str | None = None) -> None:
        when = when or now_iso()
        with self.conn:
            self.conn.execute(
                """UPDATE episodes
                   SET status = 'uploaded', uploaded_hash = ?, content_hash = ?,
                       last_error = NULL, updated_at = ?, last_synced_at = ?
                   WHERE episode_id = ?""",
                (content_hash, content_hash, when, when, episode_id),
            )

    def remove(self, episode_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM episodes WHERE episode_id = ?", (episode_id,))
