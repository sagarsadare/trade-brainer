"""SQLite persistence for PCR snapshots.

One row per (session_date, slot, expiry_kind). Raw OI/volume sums are stored
alongside the ratios so any PCR definition can be recomputed later without
re-hitting the API.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS pcr_snapshot (
    session_date TEXT    NOT NULL,          -- IST trading date, YYYY-MM-DD
    slot         TEXT    NOT NULL,          -- 'HH:MM' IST 15-minute mark
    expiry_kind  TEXT    NOT NULL,          -- 'weekly' | 'monthly'
    expiry_date  TEXT    NOT NULL,
    captured_at  TEXT    NOT NULL,          -- ISO8601 IST of the observation
    spot         REAL,
    atm_strike   REAL,
    n_strikes    INTEGER NOT NULL,
    ce_oi        INTEGER NOT NULL,
    pe_oi        INTEGER NOT NULL,
    ce_volume    INTEGER NOT NULL,
    pe_volume    INTEGER NOT NULL,
    oi_pcr       REAL,
    vol_pcr      REAL,
    max_pain     REAL,                      -- strike where writers lose least
    source       TEXT    NOT NULL,          -- 'live' | 'backfill'
    PRIMARY KEY (session_date, slot, expiry_kind)
);

-- Per-strike detail, so the strike-wise PCR curve and max pain can be redrawn
-- for any past slot instead of only for the live chain.
CREATE TABLE IF NOT EXISTS chain_strike (
    session_date TEXT    NOT NULL,
    slot         TEXT    NOT NULL,
    expiry_kind  TEXT    NOT NULL,
    strike       REAL    NOT NULL,
    ce_oi        INTEGER NOT NULL,
    pe_oi        INTEGER NOT NULL,
    ce_volume    INTEGER NOT NULL,
    pe_volume    INTEGER NOT NULL,
    PRIMARY KEY (session_date, slot, expiry_kind, strike)
);

CREATE INDEX IF NOT EXISTS idx_strike_slot
    ON chain_strike (session_date, slot, expiry_kind);

CREATE INDEX IF NOT EXISTS idx_pcr_date ON pcr_snapshot (session_date);

CREATE TABLE IF NOT EXISTS collection_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at       TEXT NOT NULL,
    kind         TEXT NOT NULL,             -- 'live' | 'backfill'
    session_date TEXT,
    slot         TEXT,
    status       TEXT NOT NULL,             -- 'ok' | 'error' | 'skipped'
    detail       TEXT
);
"""

SNAPSHOT_COLUMNS = (
    "session_date", "slot", "expiry_kind", "expiry_date", "captured_at",
    "spot", "atm_strike", "n_strikes", "ce_oi", "pe_oi", "ce_volume",
    "pe_volume", "oi_pcr", "vol_pcr", "max_pain", "source",
)

STRIKE_COLUMNS = ("session_date", "slot", "expiry_kind", "strike",
                  "ce_oi", "pe_oi", "ce_volume", "pe_volume")


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or SETTINGS.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")     # collector writes while API reads
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.executescript(SCHEMA)
        # Databases created before max_pain existed are migrated in place
        # rather than rebuilt -- the PCR history is not reproducible.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pcr_snapshot)")}
        if "max_pain" not in cols:
            conn.execute("ALTER TABLE pcr_snapshot ADD COLUMN max_pain REAL")


def upsert_snapshots(rows: Iterable[dict[str, Any]], db_path: Path | None = None) -> int:
    """Insert or replace snapshot rows. Re-running a slot overwrites it."""
    payload = [tuple(r.get(c) for c in SNAPSHOT_COLUMNS) for r in rows]
    if not payload:
        return 0
    placeholders = ",".join("?" * len(SNAPSHOT_COLUMNS))
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO pcr_snapshot ({','.join(SNAPSHOT_COLUMNS)}) "
            f"VALUES ({placeholders})", payload)
    return len(payload)


def upsert_strikes(rows: Iterable[dict[str, Any]], db_path: Path | None = None) -> int:
    payload = [tuple(r.get(c) for c in STRIKE_COLUMNS) for r in rows]
    if not payload:
        return 0
    placeholders = ",".join("?" * len(STRIKE_COLUMNS))
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO chain_strike ({','.join(STRIKE_COLUMNS)}) "
            f"VALUES ({placeholders})", payload)
    return len(payload)


def fetch_strikes(session_date: str, slot: str, expiry_kind: str,
                  db_path: Path | None = None) -> list[dict[str, Any]]:
    with connection(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM chain_strike WHERE session_date=? AND slot=? AND "
            "expiry_kind=? ORDER BY strike", (session_date, slot, expiry_kind))
        return [dict(r) for r in cur.fetchall()]


def slots_with_strikes(session_date: str, expiry_kind: str,
                       db_path: Path | None = None) -> list[str]:
    with connection(db_path) as conn:
        cur = conn.execute(
            "SELECT DISTINCT slot FROM chain_strike WHERE session_date=? AND "
            "expiry_kind=? ORDER BY slot", (session_date, expiry_kind))
        return [r["slot"] for r in cur.fetchall()]


def log_run(kind: str, status: str, detail: str = "", session_date: str | None = None,
            slot: str | None = None, db_path: Path | None = None) -> None:
    from .config import now_ist
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO collection_log (ran_at, kind, session_date, slot, status, detail) "
            "VALUES (?,?,?,?,?,?)",
            (now_ist().isoformat(timespec="seconds"), kind, session_date, slot, status, detail[:2000]))


def fetch_day(session_date: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connection(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM pcr_snapshot WHERE session_date = ? ORDER BY slot, expiry_kind",
            (session_date,))
        return [dict(r) for r in cur.fetchall()]


def available_dates(limit: int = 60, db_path: Path | None = None) -> list[str]:
    with connection(db_path) as conn:
        cur = conn.execute(
            "SELECT session_date, COUNT(*) AS n FROM pcr_snapshot "
            "GROUP BY session_date ORDER BY session_date DESC LIMIT ?", (limit,))
        return [r["session_date"] for r in cur.fetchall()]


def previous_session_date(before: str, db_path: Path | None = None) -> str | None:
    """The most recent stored trading date strictly before `before`."""
    with connection(db_path) as conn:
        cur = conn.execute(
            "SELECT session_date FROM pcr_snapshot WHERE session_date < ? "
            "ORDER BY session_date DESC LIMIT 1", (before,))
        row = cur.fetchone()
        return row["session_date"] if row else None


def latest_runs(limit: int = 20, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connection(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM collection_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def has_day(session_date: str, db_path: Path | None = None) -> bool:
    with connection(db_path) as conn:
        cur = conn.execute(
            "SELECT 1 FROM pcr_snapshot WHERE session_date = ? LIMIT 1", (session_date,))
        return cur.fetchone() is not None
