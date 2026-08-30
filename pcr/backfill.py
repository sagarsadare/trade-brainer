"""Reconstruct a past session's 15-minute PCR from Kite historical OI candles.

Requires the Historical Data add-on on the Kite Connect app; without it the
option-chain calls return a permission error and no past intraday OI exists
anywhere in the Kite API.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from .config import (IST, SESSION_OPEN, SETTINGS, SPOT_SYMBOL, now_ist, slots_for)
from .instruments import Chain, build_chains
from .kite_client import KiteSession, get_session
from .pcr import (PcrPoint, candle_slot, max_pain, points_from_candles,
                  strike_rows_from_candles, to_slot_series)
from .store import log_run, upsert_snapshots, upsert_strikes

log = logging.getLogger(__name__)


def nifty_index_token(session: KiteSession) -> int:
    """Resolve the NIFTY 50 index token from a quote rather than hard-coding it."""
    payload = session.quote([SPOT_SYMBOL])
    entry = payload.get(SPOT_SYMBOL)
    if not entry:
        raise RuntimeError(f"Kite returned no quote for {SPOT_SYMBOL}")
    return int(entry["instrument_token"])


def _day_bounds(on: date) -> tuple[datetime, datetime]:
    start = datetime.combine(on, SESSION_OPEN, tzinfo=IST)
    end = datetime.combine(on, SETTINGS.session_close, tzinfo=IST) + timedelta(minutes=15)
    return start, end


def spot_by_slot(session: KiteSession, on: date) -> dict[str, float]:
    """Nifty 50 close for each 15-minute slot, used to re-derive ATM per slot."""
    start, end = _day_bounds(on)
    candles = session.historical(nifty_index_token(session), start, end, "15minute", oi=False)
    return {candle_slot(c["date"]): float(c["close"]) for c in candles}


def _union_legs(chain: Chain, spots: list[float], half_width: int):
    """Every leg the ATM window could touch across the day's spot range.

    Fetching the union once is far cheaper than re-deriving per slot, and the
    per-slot window is then sliced out of it in `points_from_candles`.
    """
    if not spots:
        return []
    wanted = {}
    for spot in (min(spots), max(spots)):
        for leg in chain.window(spot, half_width):
            wanted[leg.token] = leg
    # Fill the gap between the two edge windows.
    lo, hi = min(spots), max(spots)
    for leg in chain.legs.values():
        if lo - half_width * 50 <= leg.strike <= hi + half_width * 50:
            wanted[leg.token] = leg
    return list(wanted.values())


STALE_EXPIRY_GAP_DAYS = 7


def stale_expiry_reason(on: date, chains: dict[str, Chain]) -> str | None:
    """Detect that the session's true front contract is missing from the dump.

    Nifty weeklies are ~7 days apart, so a resolved weekly expiring 7+ days
    after the session means the contract that was actually the front weekly
    that day has since expired and been delisted. Backfilling anyway would
    label the *next* week's contract as "weekly" and quietly corrupt the
    series, so this is a refusal rather than a warning.
    """
    weekly = chains.get("weekly")
    if weekly is None:
        return None
    gap = (weekly.expiry - on).days
    if gap >= STALE_EXPIRY_GAP_DAYS:
        return (f"resolved weekly expiry {weekly.expiry} is {gap} days after {on}; "
                f"the front contract live that day has expired and is no longer "
                f"listed. No archived dump exists for {on}.")
    return None


def backfill_day(on: date, session: KiteSession | None = None,
                 persist: bool = True, allow_stale_expiry: bool = False) -> list[PcrPoint]:
    """Rebuild every 15-minute PCR point for `on` and store it."""
    session = session or get_session()
    start, end = _day_bounds(on)

    spots = spot_by_slot(session, on)
    if not spots:
        log.warning("No Nifty 50 candles for %s - market holiday or weekend.", on)
        if persist:
            log_run("backfill", "skipped", "no index candles", on.isoformat())
        return []

    chains = build_chains(session, on=on)
    reason = stale_expiry_reason(on, chains)
    if reason and not allow_stale_expiry:
        log.warning("Refusing to backfill %s: %s", on, reason)
        if persist:
            log_run("backfill", "skipped", f"stale expiry: {reason}", on.isoformat())
        return []
    if reason:
        log.warning("Backfilling %s with a stale expiry anyway: %s", on, reason)

    slots = [s for s in slots_for() if s in spots]
    spot_values = list(spots.values())
    captured = now_ist()

    all_points: list[PcrPoint] = []
    strike_rows: list[dict] = []
    for kind, chain in chains.items():
        legs = _union_legs(chain, spot_values, SETTINGS.strike_window)
        log.info("Backfill %s %s (expiry %s): %d legs over %d slots",
                 on, kind, chain.expiry, len(legs), len(slots))
        series: dict[int, dict[str, tuple[int, int]]] = {}
        for i, leg in enumerate(legs, 1):
            candles = session.historical(leg.token, start, end, "15minute", oi=True)
            if candles:
                series[leg.token] = to_slot_series(candles)
            if i % 25 == 0:
                log.info("  ...%d/%d legs fetched", i, len(legs))
        points = points_from_candles(
            chain, on, slots, spots, series, SETTINGS.strike_window, captured)
        for point in points:
            spot = spots.get(point.slot)
            if spot is None:
                continue
            rows = strike_rows_from_candles(chain, point.slot, spot,
                                            SETTINGS.strike_window, series)
            point.max_pain, _ = max_pain(rows)
            for r in rows:
                strike_rows.append({"session_date": on.isoformat(), "slot": point.slot,
                                    "expiry_kind": kind, "strike": r.strike,
                                    "ce_oi": r.ce_oi, "pe_oi": r.pe_oi,
                                    "ce_volume": r.ce_volume, "pe_volume": r.pe_volume})
        all_points.extend(points)

    if persist:
        n = upsert_snapshots([p.as_row() for p in all_points])
        upsert_strikes(strike_rows)
        log_run("backfill", "ok" if n else "skipped",
                f"{n} rows across {len(slots)} slots", on.isoformat())
    return all_points


def previous_trading_day(reference: date | None = None) -> date:
    """Walk back over the weekend. NSE holidays surface as an empty result."""
    day = (reference or now_ist().date()) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def backfill_previous_day(session: KiteSession | None = None) -> list[PcrPoint]:
    return backfill_day(previous_trading_day(), session=session)
