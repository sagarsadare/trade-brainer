"""Persistence for spread baskets and their legs (paper and live)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pcr.config import SETTINGS, now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS spread_basket (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    mode          TEXT    NOT NULL,      -- 'paper' | 'live'
    status        TEXT    NOT NULL,      -- 'open' | 'partial' | 'failed' | 'closed'
    index_key     TEXT    NOT NULL,
    option_type   TEXT    NOT NULL,
    spot          REAL,
    weekly_expiry TEXT,
    monthly_expiry TEXT,
    gap_points    REAL,
    buy_premium   REAL,
    sell_premium  REAL,
    net_debit     REAL,
    margin_json   TEXT,
    plan_json     TEXT    NOT NULL,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS spread_leg (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    basket_id     INTEGER NOT NULL REFERENCES spread_basket(id),
    role          TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    exchange      TEXT    NOT NULL,
    tradingsymbol TEXT    NOT NULL,
    expiry        TEXT    NOT NULL,
    strike        REAL    NOT NULL,
    option_type   TEXT    NOT NULL,
    lots          INTEGER NOT NULL,
    quantity      INTEGER NOT NULL,
    premium       REAL    NOT NULL,
    order_id      TEXT,                  -- Kite order id, null in paper mode
    status        TEXT    NOT NULL,      -- 'paper' | 'placed' | 'rejected'
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_leg_basket ON spread_leg (basket_id);
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


def save_basket(plan: dict[str, Any], mode: str, status: str,
                margin: dict[str, Any] | None = None, note: str = "",
                db_path: Path | None = None) -> int:
    with connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO spread_basket
               (created_at, mode, status, index_key, option_type, spot, weekly_expiry,
                monthly_expiry, gap_points, buy_premium, sell_premium, net_debit,
                margin_json, plan_json, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now_ist().isoformat(timespec="seconds"), mode, status, plan["index"],
             plan["option_type"], plan["spot"], plan["weekly_expiry"],
             plan["monthly_expiry"], plan["gap_points"], plan["buy_premium_total"],
             plan["sell_premium_total"], plan["net_debit"],
             json.dumps(margin) if margin else None, json.dumps(plan), note))
        return int(cur.lastrowid)


def save_leg(basket_id: int, leg: dict[str, Any], status: str,
             order_id: str | None = None, error: str | None = None,
             db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.execute(
            """INSERT INTO spread_leg
               (basket_id, role, action, exchange, tradingsymbol, expiry, strike,
                option_type, lots, quantity, premium, order_id, status, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (basket_id, leg["role"], leg["action"], leg["exchange"],
             leg["tradingsymbol"], leg["expiry"], leg["strike"], leg["option_type"],
             leg["lots"], leg["quantity"], leg["premium"], order_id, status,
             error[:1000] if error else None))


def set_basket_status(basket_id: int, status: str, db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.execute("UPDATE spread_basket SET status = ? WHERE id = ?",
                     (status, basket_id))


def list_baskets(limit: int = 50, mode: str | None = None,
                 db_path: Path | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM spread_basket"
    args: list[Any] = []
    if mode:
        sql += " WHERE mode = ?"
        args.append(mode)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connection(db_path) as conn:
        baskets = [dict(r) for r in conn.execute(sql, args).fetchall()]
        for b in baskets:
            b["legs"] = [dict(r) for r in conn.execute(
                "SELECT * FROM spread_leg WHERE basket_id = ? ORDER BY id", (b["id"],))]
            b.pop("plan_json", None)
            b["margin"] = json.loads(b.pop("margin_json") or "null")
    return baskets
