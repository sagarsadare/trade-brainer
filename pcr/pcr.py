"""PCR computation from two very different Kite data shapes.

Aligning the two is the subtle part:

* ``quote()`` returns a point-in-time snapshot: ``oi`` is open interest right
  now, ``volume`` is the day's *cumulative* traded quantity.
* ``historical_data(..., oi=True)`` returns 15-minute candles where ``oi`` is
  the value at the candle's close and ``volume`` is that candle's own volume.

So the backfill must (a) cumulatively sum candle volume from the open, and
(b) shift candle labels forward by one interval, because the candle stamped
09:15 describes the state at 09:30. Both paths then mean the same thing:
"open interest and traded volume as at HH:MM".
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from .config import SLOT_MINUTES
from .instruments import Chain, OptionLeg

log = logging.getLogger(__name__)


@dataclass
class PcrPoint:
    """One expiry's aggregated option-chain state at one 15-minute slot."""
    session_date: str
    slot: str
    expiry_kind: str
    expiry_date: str
    captured_at: str
    spot: float | None
    atm_strike: float | None
    n_strikes: int
    ce_oi: int
    pe_oi: int
    ce_volume: int
    pe_volume: int
    oi_pcr: float | None
    vol_pcr: float | None
    source: str
    max_pain: float | None = None

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def ratio(put: float, call: float) -> float | None:
    """PCR = puts / calls, or None when the denominator is empty."""
    return round(put / call, 4) if call else None


def _quote_volume(payload: Mapping[str, Any]) -> int:
    """Kite has shipped both 'volume' and 'volume_traded' in quote payloads."""
    for key in ("volume", "volume_traded", "last_quantity"):
        if key in payload and payload[key] is not None:
            return int(payload[key])
    return 0


def aggregate_quotes(legs: Sequence[OptionLeg],
                     quotes: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """Sum OI and cumulative volume across a leg list.

    Returns (ce_oi, pe_oi, ce_volume, pe_volume, strikes_covered). Legs with no
    quote payload are skipped rather than counted as zero, so an illiquid strike
    that Kite omits does not silently deflate one side of the ratio.
    """
    ce_oi = pe_oi = ce_vol = pe_vol = 0
    seen: set[float] = set()
    for leg in legs:
        payload = quotes.get(leg.symbol)
        if not payload:
            continue
        oi = int(payload.get("oi") or 0)
        vol = _quote_volume(payload)
        if leg.option_type == "CE":
            ce_oi += oi
            ce_vol += vol
        else:
            pe_oi += oi
            pe_vol += vol
        seen.add(leg.strike)
    return ce_oi, pe_oi, ce_vol, pe_vol, len(seen)


def point_from_quotes(chain: Chain, legs: Sequence[OptionLeg], quotes: Mapping[str, Any],
                      spot: float, session_date: date, slot: str,
                      captured_at: datetime) -> PcrPoint:
    ce_oi, pe_oi, ce_vol, pe_vol, n = aggregate_quotes(legs, quotes)
    return PcrPoint(
        session_date=session_date.isoformat(),
        slot=slot,
        expiry_kind=chain.kind,
        expiry_date=chain.expiry.isoformat(),
        captured_at=captured_at.isoformat(timespec="seconds"),
        spot=round(spot, 2),
        atm_strike=chain.atm_strike(spot),
        n_strikes=n,
        ce_oi=ce_oi, pe_oi=pe_oi, ce_volume=ce_vol, pe_volume=pe_vol,
        oi_pcr=ratio(pe_oi, ce_oi), vol_pcr=ratio(pe_vol, ce_vol),
        source="live",
    )


# --------------------------------------------------------------- backfill --

def candle_slot(candle_time: datetime) -> str:
    """Map a 15-minute candle's start stamp to the slot it *describes*.

    The candle opening at 09:15 closes at 09:30, so its OI is the 09:30 state.
    """
    return (candle_time + timedelta(minutes=SLOT_MINUTES)).strftime("%H:%M")


def to_slot_series(candles: Iterable[Mapping[str, Any]]) -> dict[str, tuple[int, int]]:
    """Turn one instrument's candles into {slot: (oi_at_slot, cumulative_volume)}."""
    running = 0
    out: dict[str, tuple[int, int]] = {}
    for c in sorted(candles, key=lambda x: x["date"]):
        running += int(c.get("volume") or 0)
        out[candle_slot(c["date"])] = (int(c.get("oi") or 0), running)
    return out


def strike_rows_from_candles(chain: Chain, slot: str, spot: float, half_width: int,
                             series_by_token: Mapping[int, dict[str, tuple[int, int]]]
                             ) -> list["StrikeRow"]:
    """Per-strike detail for one backfilled slot, same window as the aggregate."""
    by_strike: dict[float, StrikeRow] = {}
    for leg in chain.window(spot, half_width):
        entry = series_by_token.get(leg.token, {}).get(slot)
        if entry is None:
            continue
        oi, vol = entry
        row = by_strike.setdefault(leg.strike, StrikeRow(strike=leg.strike))
        if leg.option_type == "CE":
            row.ce_oi += oi
            row.ce_volume += vol
        else:
            row.pe_oi += oi
            row.pe_volume += vol
    return [by_strike[k] for k in sorted(by_strike)]


def points_from_candles(chain: Chain, session_date: date, slots: Sequence[str],
                        spot_by_slot: Mapping[str, float],
                        series_by_token: Mapping[int, dict[str, tuple[int, int]]],
                        half_width: int, captured_at: datetime) -> list[PcrPoint]:
    """Rebuild each slot's PCR, re-selecting the ATM window per slot.

    The ATM window is recomputed from that slot's spot rather than fixed for
    the day, so a backfilled curve is built the same way the live collector
    would have built it in real time.
    """
    points: list[PcrPoint] = []
    for slot in slots:
        spot = spot_by_slot.get(slot)
        if spot is None:
            continue
        legs = chain.window(spot, half_width)
        ce_oi = pe_oi = ce_vol = pe_vol = 0
        seen: set[float] = set()
        for leg in legs:
            entry = series_by_token.get(leg.token, {}).get(slot)
            if entry is None:
                continue
            oi, vol = entry
            if leg.option_type == "CE":
                ce_oi += oi; ce_vol += vol
            else:
                pe_oi += oi; pe_vol += vol
            seen.add(leg.strike)
        if not seen:
            continue
        points.append(PcrPoint(
            session_date=session_date.isoformat(),
            slot=slot,
            expiry_kind=chain.kind,
            expiry_date=chain.expiry.isoformat(),
            captured_at=captured_at.isoformat(timespec="seconds"),
            spot=round(spot, 2),
            atm_strike=chain.atm_strike(spot),
            n_strikes=len(seen),
            ce_oi=ce_oi, pe_oi=pe_oi, ce_volume=ce_vol, pe_volume=pe_vol,
            oi_pcr=ratio(pe_oi, ce_oi), vol_pcr=ratio(pe_vol, ce_vol),
            source="backfill",
        ))
    return points


# ----------------------------------------------------------- strike detail --

@dataclass
class StrikeRow:
    """Per-strike CE/PE open interest and volume for one slot and expiry."""
    strike: float
    ce_oi: int = 0
    pe_oi: int = 0
    ce_volume: int = 0
    pe_volume: int = 0

    @property
    def oi_pcr(self) -> float | None:
        return ratio(self.pe_oi, self.ce_oi)

    @property
    def vol_pcr(self) -> float | None:
        return ratio(self.pe_volume, self.ce_volume)

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(oi_pcr=self.oi_pcr, vol_pcr=self.vol_pcr)
        return d


def strike_rows_from_quotes(legs: Sequence[OptionLeg],
                            quotes: Mapping[str, Any]) -> list[StrikeRow]:
    """Collapse quoted legs into one row per strike."""
    by_strike: dict[float, StrikeRow] = {}
    for leg in legs:
        payload = quotes.get(leg.symbol)
        if not payload:
            continue
        row = by_strike.setdefault(leg.strike, StrikeRow(strike=leg.strike))
        oi = int(payload.get("oi") or 0)
        vol = _quote_volume(payload)
        if leg.option_type == "CE":
            row.ce_oi += oi
            row.ce_volume += vol
        else:
            row.pe_oi += oi
            row.pe_volume += vol
    return [by_strike[k] for k in sorted(by_strike)]


def max_pain(rows: Sequence[StrikeRow]) -> tuple[float | None, list[dict[str, Any]]]:
    """The strike where option writers lose least if expiry settled there.

    For each candidate settlement K, writers pay the intrinsic value of every
    in-the-money contract:

        CE side: sum over strikes Ki < K of ce_oi(Ki) * (K - Ki)
        PE side: sum over strikes Kj > K of pe_oi(Kj) * (Kj - K)

    Max pain is the K minimising that total. Only listed strikes are tested --
    the true minimum can sit between two strikes, but the convention (and every
    published max-pain figure) uses the listed grid.
    """
    if not rows:
        return None, []
    curve: list[dict[str, Any]] = []
    for candidate in rows:
        k = candidate.strike
        ce_pain = sum(r.ce_oi * (k - r.strike) for r in rows if r.strike < k)
        pe_pain = sum(r.pe_oi * (r.strike - k) for r in rows if r.strike > k)
        curve.append({"strike": k, "ce_pain": int(ce_pain), "pe_pain": int(pe_pain),
                      "total_pain": int(ce_pain + pe_pain)})
    best = min(curve, key=lambda c: c["total_pain"])
    return best["strike"], curve
