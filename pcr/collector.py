"""Live 15-minute PCR snapshot job."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from .config import SETTINGS, SPOT_SYMBOL, is_session_time, now_ist, slot_label
from .instruments import build_chains
from .kite_client import KiteSession, get_session
from .pcr import (PcrPoint, max_pain, point_from_quotes,
                  strike_rows_from_quotes)
from .store import log_run, upsert_snapshots, upsert_strikes

log = logging.getLogger(__name__)


def fetch_spot(session: KiteSession) -> float:
    payload = session.quote([SPOT_SYMBOL])
    entry = payload.get(SPOT_SYMBOL)
    if not entry:
        raise RuntimeError(f"Kite returned no quote for {SPOT_SYMBOL}")
    return float(entry["last_price"])


def collect_snapshot(session: KiteSession | None = None,
                     moment: datetime | None = None,
                     persist: bool = True) -> list[PcrPoint]:
    """Capture one 15-minute PCR point per expiry and store it.

    Two quote calls: spot first (the ATM window depends on it), then every leg
    of both chains in a single batched call.
    """
    session = session or get_session()
    moment = moment or now_ist()
    slot = slot_label(moment)
    today: date = moment.date()

    spot = fetch_spot(session)
    chains = build_chains(session, on=today)

    legs_by_kind = {k: c.window(spot, SETTINGS.strike_window) for k, c in chains.items()}
    symbols = sorted({leg.symbol for legs in legs_by_kind.values() for leg in legs})
    log.info("Slot %s: spot=%.2f, quoting %d option legs", slot, spot, len(symbols))
    quotes = session.quote(symbols)

    points: list[PcrPoint] = []
    strike_rows: list[dict] = []
    for kind, legs in legs_by_kind.items():
        point = point_from_quotes(chains[kind], legs, quotes, spot, today, slot, moment)
        rows = strike_rows_from_quotes(legs, quotes)
        point.max_pain, _ = max_pain(rows)
        points.append(point)
        for r in rows:
            strike_rows.append({"session_date": today.isoformat(), "slot": slot,
                                "expiry_kind": kind, "strike": r.strike,
                                "ce_oi": r.ce_oi, "pe_oi": r.pe_oi,
                                "ce_volume": r.ce_volume, "pe_volume": r.pe_volume})
    if persist:
        upsert_snapshots([p.as_row() for p in points])
        upsert_strikes(strike_rows)
        detail = "; ".join(
            f"{p.expiry_kind}({p.expiry_date}) oi_pcr={p.oi_pcr} vol_pcr={p.vol_pcr} n={p.n_strikes}"
            for p in points)
        log_run("live", "ok", detail, today.isoformat(), slot)
    return points


def safe_collect() -> None:
    """Scheduler entry point: never let one bad tick kill the job."""
    moment = now_ist()
    if not is_session_time(moment):
        # The 15-minute cron necessarily brackets the session edges (09:00,
        # 15:45); those ticks are not collection points.
        log.debug("Outside session at %s; skipping tick.", moment.strftime("%H:%M"))
        return
    try:
        points = collect_snapshot(moment=moment)
        for p in points:
            log.info("%s %s %-7s oi_pcr=%s vol_pcr=%s", p.session_date, p.slot,
                     p.expiry_kind, p.oi_pcr, p.vol_pcr)
    except Exception as exc:                       # noqa: BLE001 - scheduler guard
        log.exception("Live collection failed at %s", slot_label(moment))
        try:
            log_run("live", "error", repr(exc), moment.date().isoformat(), slot_label(moment))
        except Exception:                          # noqa: BLE001
            log.exception("Could not write failure to collection_log")
