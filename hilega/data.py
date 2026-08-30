"""Candle fetch and timeframe handling for the Hilega Milega engine."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from pcr.config import IST, now_ist
from pcr.kite_client import KiteSession
from .strategy import Candle

log = logging.getLogger(__name__)

# Kite intervals we expose, with the higher timeframe each one confirms against
# (rules S.6/S.8). "week" is not a Kite interval -- it is resampled from daily.
TIMEFRAMES: dict[str, dict[str, Any]] = {
    "5minute":  {"label": "5 min",  "higher": "15minute", "max_days": 60,   "bars_per_day": 75},
    "15minute": {"label": "15 min", "higher": "60minute", "max_days": 180,  "bars_per_day": 25},
    "60minute": {"label": "1 hour", "higher": "day",      "max_days": 365,  "bars_per_day": 7},
    "day":      {"label": "Daily",  "higher": "week",     "max_days": 1500, "bars_per_day": 1},
    "week":     {"label": "Weekly", "higher": "month",    "max_days": 3000, "bars_per_day": 0.2},
}


def higher_timeframe(tf: str) -> str:
    return TIMEFRAMES.get(tf, {}).get("higher", "day")


def _to_candle(row: dict[str, Any]) -> Candle:
    d = row["date"]
    stamp = d.isoformat() if hasattr(d, "isoformat") else str(d)
    return Candle(date=stamp, open=float(row["open"]), high=float(row["high"]),
                  low=float(row["low"]), close=float(row["close"]),
                  volume=int(row.get("volume") or 0))


def fetch(session: KiteSession, token: int, interval: str,
          bars: int = 400) -> list[Candle]:
    """Roughly `bars` candles of `interval`, ending now.

    Weekly and monthly are resampled from daily -- Kite has no native interval
    for either (pseudocode S.12 flags this).
    """
    if interval in ("week", "month"):
        per_bar = 5 if interval == "week" else 21
        daily = fetch(session, token, "day", bars=bars * per_bar + 60)
        return resample(daily, interval)

    spec = TIMEFRAMES.get(interval) or TIMEFRAMES["day"]
    # Calendar days needed, padded for weekends and holidays.
    days = int(bars / spec["bars_per_day"] * 1.6) + 10
    days = min(days, spec["max_days"])
    to_dt = now_ist()
    from_dt = to_dt - timedelta(days=days)
    rows = session.historical(token, from_dt, to_dt, interval, oi=False)
    out = [_to_candle(r) for r in rows]
    log.info("Fetched %d %s candles for token %s", len(out), interval, token)
    return out[-bars:] if len(out) > bars else out


def resample(daily: Sequence[Candle], interval: str = "week") -> list[Candle]:
    """Collapse daily candles into ISO weeks or calendar months.

    Each bucket keeps first open, max high, min low, last close, summed volume,
    and is stamped with its LAST trading day -- so the bar is dated when it
    actually completed, not when it opened.
    """
    buckets: dict[tuple, list[Candle]] = {}
    order: list[tuple] = []
    for c in daily:
        d = datetime.fromisoformat(c.date).date() if "T" in c.date or "-" in c.date else None
        if d is None:
            continue
        key = d.isocalendar()[:2] if interval == "week" else (d.year, d.month)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(c)

    out: list[Candle] = []
    for key in order:
        grp = buckets[key]
        out.append(Candle(
            date=grp[-1].date,
            open=grp[0].open,
            high=max(x.high for x in grp),
            low=min(x.low for x in grp),
            close=grp[-1].close,
            volume=sum(x.volume for x in grp),
        ))
    return out
