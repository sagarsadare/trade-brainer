"""Live option chain: strike x expiry with premium, OI and volume.

The playbook (S.2) wants premium at each strike across two expiries side by
side, roughly 1000 points ITM to 1000 points OTM. Greeks and OI are not needed
for leg selection, but OI/volume come free in the same quote and are worth
showing as a liquidity check before sending an order.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Sequence

from pcr.kite_client import KiteSession
from .universe import IndexSpec, load_instruments, lot_size

log = logging.getLogger(__name__)


@dataclass
class ChainRow:
    strike: float
    option_type: str
    tradingsymbol: str
    exchange: str
    token: int
    expiry: str
    premium: float          # last traded price
    bid: float
    ask: float
    oi: int
    volume: int
    lot_size: int

    @property
    def symbol(self) -> str:
        return f"{self.exchange}:{self.tradingsymbol}"

    @property
    def spread_pct(self) -> float | None:
        """Bid-ask spread as a fraction of the mid, the liquidity check."""
        if self.bid <= 0 or self.ask <= 0:
            return None
        mid = (self.bid + self.ask) / 2
        return round((self.ask - self.bid) / mid, 4) if mid else None

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["spread_pct"] = self.spread_pct
        return d


def fetch_spot(session: KiteSession, spec: IndexSpec) -> float:
    payload = session.quote([spec.spot_symbol])
    entry = payload.get(spec.spot_symbol)
    if not entry:
        raise RuntimeError(f"Kite returned no quote for {spec.spot_symbol}")
    return float(entry["last_price"])


def _depth_top(payload: dict[str, Any], side: str) -> float:
    levels = (payload.get("depth") or {}).get(side) or []
    return float(levels[0]["price"]) if levels else 0.0


def build_chain(session: KiteSession, spec: IndexSpec, expiry: date, option_type: str,
                spot: float, span_points: float | None = None) -> list[ChainRow]:
    """Every listed strike of one expiry within +/- span_points of spot."""
    rows = load_instruments(session, spec)
    lots = lot_size(rows)
    span = span_points if span_points is not None else spot * spec.max_gap_pct
    lo, hi = spot - span, spot + span

    legs = [r for r in rows
            if r["expiry"] == expiry.isoformat()
            and r["instrument_type"] == option_type
            and lo <= r["strike"] <= hi]
    if not legs:
        return []

    quotes = session.quote([f"{spec.exchange}:{r['tradingsymbol']}" for r in legs])
    out: list[ChainRow] = []
    for r in legs:
        sym = f"{spec.exchange}:{r['tradingsymbol']}"
        q = quotes.get(sym)
        if not q:
            continue        # illiquid / not quoted; omit rather than fake a zero
        out.append(ChainRow(
            strike=r["strike"], option_type=option_type,
            tradingsymbol=r["tradingsymbol"], exchange=spec.exchange,
            token=r["instrument_token"], expiry=expiry.isoformat(),
            premium=float(q.get("last_price") or 0.0),
            bid=_depth_top(q, "buy"), ask=_depth_top(q, "sell"),
            oi=int(q.get("oi") or 0), volume=int(q.get("volume") or 0),
            lot_size=r.get("lot_size", lots)))
    out.sort(key=lambda x: x.strike)
    log.info("%s %s %s chain: %d strikes quoted around spot %.2f",
             spec.key, expiry, option_type, len(out), spot)
    return out


def pair_by_strike(near: Sequence[ChainRow], far: Sequence[ChainRow]) -> list[dict[str, Any]]:
    """Side-by-side weekly-vs-monthly premium table (the S.2 workflow)."""
    far_by_strike = {r.strike: r for r in far}
    table = []
    for n in near:
        f = far_by_strike.get(n.strike)
        table.append({
            "strike": n.strike,
            "near_premium": n.premium, "near_oi": n.oi, "near_volume": n.volume,
            "far_premium": f.premium if f else None,
            "far_oi": f.oi if f else None, "far_volume": f.volume if f else None,
            # What a rollover at this strike is currently worth.
            "time_value_gap": round(f.premium - n.premium, 2) if f else None,
        })
    return table
