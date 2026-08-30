"""Persistence for Hilega Milega signals and trades."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pcr.config import SETTINGS, now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS hm_trade (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    mode          TEXT NOT NULL,          -- 'paper' | 'live'
    status        TEXT NOT NULL,          -- 'open' | 'partial' | 'failed'
    symbol        TEXT NOT NULL,
    exchange      TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL,
    interval      TEXT NOT NULL,
    higher_interval TEXT NOT NULL,
    side          TEXT NOT NULL,
    bar_date      TEXT NOT NULL,
    entry         REAL, stop_loss REAL, target REAL,
    risk_reward   REAL, quantity INTEGER, risk_amount REAL,
    entry_order_id TEXT, sl_order_id TEXT,
    signal_json   TEXT NOT NULL,
    error         TEXT,
    note          TEXT,
    UNIQUE (mode, tradingsymbol, interval, bar_date, side)
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or SETTINGS.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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


def already_recorded(mode: str, tradingsymbol: str, interval: str, bar_date: str,
                     side: str, db_path: Path | None = None) -> bool:
    """S.10 of the pseudocode -- never fire the same bar's signal twice."""
    with connection(db_path) as conn:
        return conn.execute(
            "SELECT 1 FROM hm_trade WHERE mode=? AND tradingsymbol=? AND interval=? "
            "AND bar_date=? AND side=? LIMIT 1",
            (mode, tradingsymbol, interval, bar_date, side)).fetchone() is not None


def save_trade(sig: dict[str, Any], ctx: dict[str, Any], mode: str, status: str,
               entry_order_id: str | None = None, sl_order_id: str | None = None,
               error: str | None = None, note: str = "",
               db_path: Path | None = None) -> int:
    with connection(db_path) as conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO hm_trade
               (created_at, mode, status, symbol, exchange, tradingsymbol, interval,
                higher_interval, side, bar_date, entry, stop_loss, target,
                risk_reward, quantity, risk_amount, entry_order_id, sl_order_id,
                signal_json, error, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now_ist().isoformat(timespec="seconds"), mode, status, ctx["symbol"],
             ctx["exchange"], ctx["tradingsymbol"], ctx["interval"],
             ctx["higher_interval"], sig["signal"], sig["bar_date"], sig["entry"],
             sig["stop_loss"], sig["target"], sig["risk_reward"], sig["quantity"],
             sig["risk_amount"], entry_order_id, sl_order_id, json.dumps(sig),
             error[:1000] if error else None, note))
        return int(cur.lastrowid)


def list_trades(limit: int = 50, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connection(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM hm_trade ORDER BY id DESC LIMIT ?", (limit,))]
    for r in rows:
        r.pop("signal_json", None)
    return rows
