"""Tradable index universe and its option-instrument loader.

Deliberately separate from `pcr.instruments`: the PCR pipeline is Nifty-only
and running, and this layer has to span exchanges (NFO and BFO) and carry
lot sizes, which the PCR cache strips out.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from pcr.config import ROOT, now_ist
from pcr.kite_client import KiteSession

log = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "strategy_instruments"
CACHE_VERSION = 2


@dataclass(frozen=True)
class IndexSpec:
    key: str            # our identifier, e.g. 'NIFTY'
    label: str          # human label, e.g. 'Nifty 50'
    name: str           # 'name' field in the Kite instrument dump
    exchange: str       # 'NFO' | 'BFO'
    spot_symbol: str    # 'NSE:NIFTY 50'
    strike_step: float
    round_step: float   # what counts as a "round-number" strike for tie-breaks
    # Playbook's entry filter is ~1000 Nifty points. Expressed as a fraction of
    # spot so it generalises to indices trading at a different absolute level.
    max_gap_pct: float = 0.0414
    # Anchor for the fair-value zone framework (S.8): the last drawdown of
    # more than 25%, which for every Indian index is the COVID crash. Kite's
    # daily history only reaches back about four years, so these cannot be
    # derived from the API and are recorded here as published index levels.
    anchor_date: date | None = None      # the crash LOW date
    anchor_level: float | None = None    # the crash LOW
    crash_top: float | None = None       # the peak immediately before the crash
    crash_top_date: date | None = None


INDICES: dict[str, IndexSpec] = {
    "NIFTY": IndexSpec(
        key="NIFTY", label="Nifty 50", name="NIFTY", exchange="NFO",
        spot_symbol="NSE:NIFTY 50", strike_step=50.0, round_step=500.0,
        anchor_date=date(2020, 3, 24), anchor_level=7511.10,
        crash_top=12430.50, crash_top_date=date(2020, 1, 20)),
    "SENSEX": IndexSpec(
        key="SENSEX", label="Sensex", name="SENSEX", exchange="BFO",
        spot_symbol="BSE:SENSEX", strike_step=100.0, round_step=1000.0,
        anchor_date=date(2020, 3, 24), anchor_level=25638.90,
        crash_top=42273.87, crash_top_date=date(2020, 1, 20)),
    "BANKNIFTY": IndexSpec(
        key="BANKNIFTY", label="Bank Nifty", name="BANKNIFTY", exchange="NFO",
        spot_symbol="NSE:NIFTY BANK", strike_step=100.0, round_step=1000.0,
        anchor_date=date(2020, 3, 24), anchor_level=16116.25,
        crash_top=32613.10, crash_top_date=date(2020, 1, 20)),
}


def get_spec(key: str) -> IndexSpec:
    try:
        return INDICES[key.upper()]
    except KeyError:
        raise ValueError(f"Unknown index '{key}'. Known: {', '.join(INDICES)}") from None


def _cache_path(spec: IndexSpec) -> Path:
    return CACHE_DIR / f"{spec.exchange}_{spec.name}.json"


def load_instruments(session: KiteSession, spec: IndexSpec,
                     force: bool = False) -> list[dict[str, Any]]:
    """Option legs for one index, cached per trading day.

    Keeps `lot_size` (the strategy sizes in lots) and `expiry` as an ISO string.
    """
    cache, today = _cache_path(spec), now_ist().date().isoformat()
    if not force and cache.exists():
        try:
            blob = json.loads(cache.read_text())
            if blob.get("fetched_on") == today and blob.get("v") == CACHE_VERSION:
                return blob["rows"]
        except (json.JSONDecodeError, OSError, KeyError):
            log.warning("Instrument cache %s unreadable; refetching.", cache.name)

    log.info("Downloading %s instruments for %s...", spec.exchange, spec.name)
    rows = [
        {
            "instrument_token": int(r["instrument_token"]),
            "tradingsymbol": r["tradingsymbol"],
            "strike": float(r["strike"]),
            "instrument_type": r["instrument_type"],
            "expiry": r["expiry"].isoformat() if hasattr(r["expiry"], "isoformat") else str(r["expiry"]),
            "lot_size": int(r["lot_size"]),
        }
        for r in session.instruments(spec.exchange)
        if r.get("name") == spec.name and r.get("instrument_type") in ("CE", "PE") and r.get("expiry")
    ]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"v": CACHE_VERSION, "fetched_on": today, "rows": rows}))
    log.info("Cached %d %s option legs.", len(rows), spec.name)
    return rows


def lot_size(rows: Iterable[dict[str, Any]]) -> int:
    """Lot size for the index. Read from the dump, never hard-coded.

    Exchanges revise lot sizes; when a revision straddles expiries the dump
    carries both, so the most common value wins.
    """
    from collections import Counter
    sizes = Counter(r["lot_size"] for r in rows)
    if not sizes:
        raise ValueError("no instruments to read a lot size from")
    return sizes.most_common(1)[0][0]


def expiries_of(rows: Iterable[dict[str, Any]]) -> list[date]:
    return sorted({date.fromisoformat(r["expiry"]) for r in rows})
