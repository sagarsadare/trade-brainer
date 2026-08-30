"""Builds the Nifty option-chain universe: expiries, ATM window, strike lists."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from .config import (OPTION_EXCHANGE, SETTINGS, STRIKE_STEP, UNDERLYING_NAME,
                     now_ist)
from .kite_client import KiteSession

log = logging.getLogger(__name__)

EXPIRY_KINDS = ("weekly", "monthly")


@dataclass(frozen=True)
class OptionLeg:
    token: int
    tradingsymbol: str
    strike: float
    option_type: str        # 'CE' or 'PE'
    expiry: date

    @property
    def symbol(self) -> str:
        return f"{OPTION_EXCHANGE}:{self.tradingsymbol}"


@dataclass(frozen=True)
class Chain:
    """Every listed strike for one expiry, indexed for fast window slicing."""
    kind: str               # 'weekly' | 'monthly'
    expiry: date
    legs: dict[tuple[float, str], OptionLeg]

    @property
    def strikes(self) -> list[float]:
        return sorted({strike for strike, _ in self.legs})

    def window(self, spot: float, half_width: int) -> list[OptionLeg]:
        """The CE+PE legs for ATM +/- half_width strikes around `spot`.

        Only strikes that are actually listed are returned, so a window that
        runs off the end of the chain silently narrows instead of failing.
        """
        listed = self.strikes
        if not listed:
            return []
        atm = min(listed, key=lambda s: abs(s - spot))
        idx = listed.index(atm)
        lo, hi = max(0, idx - half_width), min(len(listed), idx + half_width + 1)
        out: list[OptionLeg] = []
        for strike in listed[lo:hi]:
            for opt in ("CE", "PE"):
                leg = self.legs.get((strike, opt))
                if leg is not None:
                    out.append(leg)
        return out

    def atm_strike(self, spot: float) -> float:
        listed = self.strikes
        return min(listed, key=lambda s: abs(s - spot)) if listed else round(spot / STRIKE_STEP) * STRIKE_STEP


def atm_of(spot: float) -> float:
    return round(spot / STRIKE_STEP) * STRIKE_STEP


def _load_nfo(session: KiteSession, force: bool = False) -> list[dict[str, Any]]:
    """NFO instrument dump, cached to disk for the current trading day.

    The dump is ~10 MB of JSON from Kite and only changes overnight, so
    refetching it on every 15-minute tick would be pure waste.
    """
    cache = SETTINGS.instruments_cache
    today = now_ist().date().isoformat()
    if not force and cache.exists():
        try:
            blob = json.loads(cache.read_text())
            if blob.get("fetched_on") == today:
                return blob["rows"]
        except (json.JSONDecodeError, OSError, KeyError):
            log.warning("Instrument cache unreadable; refetching.")

    log.info("Downloading %s instrument dump from Kite...", OPTION_EXCHANGE)
    rows = session.instruments(OPTION_EXCHANGE)
    slim = [
        {
            "instrument_token": r["instrument_token"],
            "tradingsymbol": r["tradingsymbol"],
            "name": r["name"],
            "strike": float(r["strike"]),
            "instrument_type": r["instrument_type"],
            "expiry": r["expiry"].isoformat() if hasattr(r["expiry"], "isoformat") else str(r["expiry"]),
            "segment": r["segment"],
        }
        for r in rows
        if r.get("name") == UNDERLYING_NAME and r.get("instrument_type") in ("CE", "PE")
    ]
    cache.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"fetched_on": today, "rows": slim})
    cache.write_text(blob)
    archive = cache.parent / "instrument_archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"nfo_{today}.json").write_text(blob)
    log.info("Cached %d %s option instruments.", len(slim), UNDERLYING_NAME)
    return slim


def load_dump_for(session: KiteSession, on: date) -> list[dict[str, Any]]:
    """Instrument rows as they were listed on `on`.

    Kite drops expired contracts from the live dump, so backfilling a past
    session that was itself an expiry day needs the archived dump from that
    day. Falls back to the current dump when no archive exists yet.
    """
    archive = SETTINGS.instruments_cache.parent / "instrument_archive" / f"nfo_{on.isoformat()}.json"
    if archive.exists():
        try:
            return json.loads(archive.read_text())["rows"]
        except (json.JSONDecodeError, OSError, KeyError):
            log.warning("Archived dump for %s unreadable; using current dump.", on)
    rows = _load_nfo(session)
    if on < now_ist().date():
        log.warning("No archived instrument dump for %s; using today's listings. "
                    "Contracts that expired since then are not recoverable.", on)
    return rows


def _expiries(rows: Iterable[dict[str, Any]]) -> list[date]:
    return sorted({date.fromisoformat(r["expiry"]) for r in rows if r["expiry"]})


def pick_expiries(all_expiries: Sequence[date], on: date) -> dict[str, date]:
    """Resolve the 'weekly' and 'monthly' expiries live on a given date.

    Weekly  = nearest expiry on/after `on`.
    Monthly = nearest month-end expiry on/after `on`. When the two collide
    (expiry week of the month) the monthly series rolls to the following
    month so the dashboard always shows two distinct curves.
    """
    upcoming = [e for e in all_expiries if e >= on]
    if not upcoming:
        raise ValueError(f"No {UNDERLYING_NAME} expiries listed on or after {on}")

    last_of_month: dict[tuple[int, int], date] = {}
    for e in all_expiries:
        key = (e.year, e.month)
        if key not in last_of_month or e > last_of_month[key]:
            last_of_month[key] = e
    monthlies = sorted(e for e in last_of_month.values() if e >= on)

    weekly = upcoming[0]
    monthly = next((m for m in monthlies if m > weekly), None)
    if monthly is None:
        monthly = monthlies[0] if monthlies else weekly
    return {"weekly": weekly, "monthly": monthly}


def build_chains(session: KiteSession, on: date | None = None,
                 force_refresh: bool = False) -> dict[str, Chain]:
    """The weekly and monthly Nifty option chains live on `on` (default: today)."""
    on = on or now_ist().date()
    if on >= now_ist().date():
        rows = _load_nfo(session, force=force_refresh)
    else:
        rows = load_dump_for(session, on)
    chosen = pick_expiries(_expiries(rows), on)

    chains: dict[str, Chain] = {}
    for kind, expiry in chosen.items():
        legs = {
            (float(r["strike"]), r["instrument_type"]): OptionLeg(
                token=int(r["instrument_token"]),
                tradingsymbol=r["tradingsymbol"],
                strike=float(r["strike"]),
                option_type=r["instrument_type"],
                expiry=expiry,
            )
            for r in rows
            if r["expiry"] == expiry.isoformat()
        }
        chains[kind] = Chain(kind=kind, expiry=expiry, legs=legs)
        log.info("%s chain: expiry %s, %d strikes", kind, expiry, len(chains[kind].strikes))
    return chains


FUTURES_CACHE = SETTINGS.instruments_cache.parent / "nfo_futures.json"


def front_future(session: KiteSession, on: date | None = None) -> dict[str, Any] | None:
    """Nearest-expiry index future, cached daily.

    The index itself carries no traded volume (Kite returns 0), so any VWAP has
    to come from the future. This is the only place futures are needed, hence a
    small separate cache rather than widening the option-chain one.
    """
    on = on or now_ist().date()
    today = on.isoformat()
    if FUTURES_CACHE.exists():
        try:
            blob = json.loads(FUTURES_CACHE.read_text())
            if blob.get("fetched_on") == now_ist().date().isoformat():
                rows = blob["rows"]
                live = [r for r in rows if r["expiry"] >= today]
                return min(live, key=lambda r: r["expiry"]) if live else None
        except (json.JSONDecodeError, OSError, KeyError):
            log.warning("Futures cache unreadable; refetching.")

    rows = [
        {"instrument_token": int(r["instrument_token"]),
         "tradingsymbol": r["tradingsymbol"],
         "expiry": r["expiry"].isoformat() if hasattr(r["expiry"], "isoformat") else str(r["expiry"]),
         "lot_size": int(r["lot_size"])}
        for r in session.instruments(OPTION_EXCHANGE)
        if r.get("name") == UNDERLYING_NAME and r.get("instrument_type") == "FUT"
    ]
    FUTURES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FUTURES_CACHE.write_text(json.dumps(
        {"fetched_on": now_ist().date().isoformat(), "rows": rows}))
    live = [r for r in rows if r["expiry"] >= today]
    log.info("Cached %d %s futures; front is %s", len(rows), UNDERLYING_NAME,
             min(live, key=lambda r: r["expiry"])["tradingsymbol"] if live else "none")
    return min(live, key=lambda r: r["expiry"]) if live else None
