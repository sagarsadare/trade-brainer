"""The maths-based calendar spread engine.

Implements the playbook: pick legs by PREMIUM PRICE, not by strike (S.3),
balance total bought premium against total sold premium at a 3:1:1 lot ratio,
and apply the entry filters (S.9) -- above all the strike-gap rule, which is
the strategy's only real risk control and is applied BEFORE entry.

Where the playbook quotes absolute Nifty points, this generalises to a
fraction of spot so Sensex (trading ~3.2x Nifty's level) gets a proportionate
threshold rather than an accidentally punitive one. That scaling is our
extension, not the source's -- it is surfaced in the output.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Sequence

from .chain import ChainRow
from .universe import IndexSpec

log = logging.getLogger(__name__)

# S.3 default premium targets, in rupees.
DEFAULT_BUY_PREMIUM = 200.0
DEFAULT_SELL_PREMIUMS = (150.0, 450.0)
DEFAULT_SELL_LOTS = (1, 1)

# A candidate within this fraction of the target premium is "close enough"
# for the round-strike tie-breaker (S.3) to apply.
ROUND_STRIKE_TOLERANCE = 0.08
MAX_PREMIUM_MISS = 0.35     # reject a leg whose best match is this far off


@dataclass
class Leg:
    role: str               # 'buy_monthly' | 'sell_weekly_1' | 'sell_weekly_2'
    action: str             # 'BUY' | 'SELL'
    expiry: str
    strike: float
    option_type: str
    tradingsymbol: str
    exchange: str
    premium: float
    target_premium: float
    lots: int
    lot_size: int
    oi: int
    volume: int
    spread_pct: float | None

    @property
    def quantity(self) -> int:
        return self.lots * self.lot_size

    @property
    def premium_value(self) -> float:
        """Rupee premium for this leg's lots (premium x quantity)."""
        return round(self.premium * self.quantity, 2)

    @property
    def premium_miss(self) -> float:
        return abs(self.premium - self.target_premium) / self.target_premium

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(quantity=self.quantity, premium_value=self.premium_value,
                 premium_miss=round(self.premium_miss, 4))
        return d


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class SpreadPlan:
    index: str
    option_type: str
    spot: float
    weekly_expiry: str | None
    monthly_expiry: str | None
    legs: list[Leg] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    zone: dict[str, Any] | None = None

    @property
    def buy_premium_total(self) -> float:
        return round(sum(l.premium_value for l in self.legs if l.action == "BUY"), 2)

    @property
    def sell_premium_total(self) -> float:
        return round(sum(l.premium_value for l in self.legs if l.action == "SELL"), 2)

    @property
    def net_debit(self) -> float:
        return round(self.buy_premium_total - self.sell_premium_total, 2)

    @property
    def balance_skew(self) -> float | None:
        """How far from the S.3 'buy premium == sell premium' ideal, as a fraction."""
        s = self.sell_premium_total
        return round((self.buy_premium_total - s) / s, 4) if s else None

    @property
    def gap_points(self) -> float | None:
        """Strike distance between the buy leg and the farthest short leg (S.5)."""
        buys = [l.strike for l in self.legs if l.action == "BUY"]
        sells = [l.strike for l in self.legs if l.action == "SELL"]
        if not buys or not sells:
            return None
        return round(max(abs(b - s) for b in buys for s in sells), 2)

    @property
    def tradeable(self) -> bool:
        return bool(self.legs) and all(c.passed for c in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "option_type": self.option_type, "spot": self.spot,
            "weekly_expiry": self.weekly_expiry, "monthly_expiry": self.monthly_expiry,
            "legs": [l.as_row() for l in self.legs],
            "checks": [asdict(c) for c in self.checks],
            "notes": self.notes, "zone": self.zone,
            "buy_premium_total": self.buy_premium_total,
            "sell_premium_total": self.sell_premium_total,
            "net_debit": self.net_debit,
            "balance_skew": self.balance_skew,
            "gap_points": self.gap_points,
            "tradeable": self.tradeable,
        }


def find_by_premium(rows: Sequence[ChainRow], target: float,
                    round_step: float) -> ChainRow | None:
    """The strike whose premium is closest to `target` (S.3).

    Among candidates all within tolerance of the target, prefer a round-number
    strike -- the playbook's explicit tie-breaker.
    """
    priced = [r for r in rows if r.premium > 0]
    if not priced:
        return None
    best = min(priced, key=lambda r: abs(r.premium - target))
    close = [r for r in priced
             if abs(r.premium - target) <= target * ROUND_STRIKE_TOLERANCE]
    rounds = [r for r in close if r.strike % round_step == 0]
    if rounds:
        return min(rounds, key=lambda r: abs(r.premium - target))
    return best


def fair_value_zone(spec: IndexSpec, spot: float, on: date | None = None,
                    cagr: float = 0.117,
                    latest_top: float | None = None,
                    latest_top_date: str | None = None) -> dict[str, Any] | None:
    """S.8 fair-value framework, used only to bias the lot ratio.

    Compounds the last crash's TOP forward at a long-run growth rate. The top,
    not the low: the low is a panic print that the market spent months undoing,
    so growing it forward understates fair value badly -- for Nifty it lands
    around 15,300 against a 24,175 spot, which would call every level since
    2023 a lifetime high. Compounding the pre-crash peak instead answers the
    real question: where would the index be had the crash never happened and
    it simply grown at trend?

    Still a crude anchor-and-compound model, not a valuation -- it exists to
    nudge the buy:sell skew and nothing more.
    """
    if spec.anchor_date is None or spec.anchor_level is None:
        return None
    on = on or date.today()
    anchor_level = spec.crash_top if spec.crash_top else spec.anchor_level
    anchor_date = spec.crash_top_date if spec.crash_top_date else spec.anchor_date
    years = (on - anchor_date).days / 365.25
    fair = anchor_level * ((1 + cagr) ** years)
    # Symmetric band: the high zone sits as far above fair as the low sits below.
    band = fair * 0.20
    high, low = fair + band, fair - band
    if spot >= high:
        zone, skew = "lifetime-high", "sell heavier, buy lighter"
    elif spot <= low:
        zone, skew = "extreme-low", "buy heavier, sell lighter"
    else:
        zone, skew = "fair-value", "keep buy:sell roughly balanced"
    drawdown = ((spec.crash_top - spec.anchor_level) / spec.crash_top
                if spec.crash_top else None)
    return {
        "zone": zone, "suggested_skew": skew,
        "as_of": on.isoformat(),
        "spot": round(spot, 2),
        "fair_value": round(fair, 2),
        "compounded_from": "crash_top",
        "anchor_level": anchor_level,
        "anchor_date": anchor_date.isoformat(),
        "latest_top": latest_top,
        "latest_top_date": latest_top_date,
        "spot_vs_latest_top_pct": (round((spot - latest_top) / latest_top, 4)
                                   if latest_top else None),
        "high_band": round(high, 2), "low_band": round(low, 2),
        # S.8 anchor: the last drawdown greater than 25%.
        "crash_year": spec.anchor_date.year,
        "crash_top": spec.crash_top,
        "crash_top_date": spec.crash_top_date.isoformat() if spec.crash_top_date else None,
        "crash_low": spec.anchor_level,
        "crash_low_date": spec.anchor_date.isoformat(),
        "crash_drawdown_pct": round(drawdown, 4) if drawdown is not None else None,
        "years_from_anchor": round(years, 2),
        "cagr": cagr,
        "spot_vs_fair_pct": round((spot - fair) / fair, 4) if fair else None,
        "anchor": f"{spec.anchor_date} @ {spec.anchor_level}",
    }


def build_plan(spec: IndexSpec, option_type: str, spot: float,
               weekly_expiry: date, monthly_expiry: date,
               weekly_rows: Sequence[ChainRow], monthly_rows: Sequence[ChainRow],
               buy_premium: float = DEFAULT_BUY_PREMIUM,
               sell_premiums: Sequence[float] = DEFAULT_SELL_PREMIUMS,
               sell_lots: Sequence[int] = DEFAULT_SELL_LOTS,
               max_gap_points: float | None = None,
               on: date | None = None) -> SpreadPlan:
    """Select legs by premium, balance lots, and run the entry filters."""
    plan = SpreadPlan(index=spec.key, option_type=option_type, spot=round(spot, 2),
                      weekly_expiry=weekly_expiry.isoformat(),
                      monthly_expiry=monthly_expiry.isoformat())
    plan.zone = fair_value_zone(spec, spot, on)

    buy_row = find_by_premium(monthly_rows, buy_premium, spec.round_step)
    sell_rows = [find_by_premium(weekly_rows, t, spec.round_step) for t in sell_premiums]

    if buy_row is None or any(r is None for r in sell_rows):
        plan.checks.append(Check("legs_found", False,
                                 "Could not find a priced strike for every target "
                                 "premium. Widen the chain span or adjust the targets."))
        return plan

    # S.3: size the buy leg so total bought premium ~= total sold premium.
    sold = sum(r.premium * lots for r, lots in zip(sell_rows, sell_lots))
    buy_lots = max(1, round(sold / buy_row.premium)) if buy_row.premium else 1

    plan.legs.append(Leg(
        role="buy_monthly", action="BUY", expiry=monthly_expiry.isoformat(),
        strike=buy_row.strike, option_type=option_type,
        tradingsymbol=buy_row.tradingsymbol, exchange=buy_row.exchange,
        premium=buy_row.premium, target_premium=buy_premium, lots=buy_lots,
        lot_size=buy_row.lot_size, oi=buy_row.oi, volume=buy_row.volume,
        spread_pct=buy_row.spread_pct))
    for i, (row, target, lots) in enumerate(zip(sell_rows, sell_premiums, sell_lots), 1):
        plan.legs.append(Leg(
            role=f"sell_weekly_{i}", action="SELL", expiry=weekly_expiry.isoformat(),
            strike=row.strike, option_type=option_type,
            tradingsymbol=row.tradingsymbol, exchange=row.exchange,
            premium=row.premium, target_premium=target, lots=lots,
            lot_size=row.lot_size, oi=row.oi, volume=row.volume,
            spread_pct=row.spread_pct))

    _run_checks(plan, spec, spot, max_gap_points)
    return plan


def _run_checks(plan: SpreadPlan, spec: IndexSpec, spot: float,
                max_gap_points: float | None) -> None:
    gap_limit = max_gap_points if max_gap_points is not None else spot * spec.max_gap_pct
    gap = plan.gap_points

    plan.checks.append(Check(
        "entry_gap", gap is not None and gap <= gap_limit,
        f"Buy-to-farthest-short strike gap is {gap:.0f} pts against a limit of "
        f"{gap_limit:.0f} pts ({spec.max_gap_pct:.2%} of spot). "
        f"S.9 says skip the trade when this is exceeded."))

    skew = plan.balance_skew
    plan.checks.append(Check(
        "premium_balance", skew is not None and abs(skew) <= 0.20,
        f"Bought premium Rs.{plan.buy_premium_total:,.0f} vs sold "
        f"Rs.{plan.sell_premium_total:,.0f} ({skew:+.1%} skew). "
        f"S.3 wants these roughly equal."))

    worst = max(plan.legs, key=lambda l: l.premium_miss)
    plan.checks.append(Check(
        "premium_match", worst.premium_miss <= MAX_PREMIUM_MISS,
        f"Worst premium match is {worst.role} at Rs.{worst.premium:.2f} "
        f"vs target Rs.{worst.target_premium:.0f} ({worst.premium_miss:.1%} off)."))

    illiquid = [l for l in plan.legs if l.spread_pct is not None and l.spread_pct > 0.10]
    plan.checks.append(Check(
        "liquidity", not illiquid,
        "All legs quote inside a 10% bid-ask spread." if not illiquid else
        "Wide bid-ask on: " + ", ".join(
            f"{l.role} ({l.spread_pct:.1%})" for l in illiquid)))

    if plan.zone and plan.zone["zone"] != "fair-value":
        plan.notes.append(
            f"Spot sits in the {plan.zone['zone']} zone - S.3 suggests you "
            f"{plan.zone['suggested_skew']} rather than run the flat 3:1:1 ratio.")
    plan.notes.append(
        "No stop-loss by design (S.5): a breached short leg is rolled to the next "
        "weekly at the same strike. The entry gap check above is the actual risk "
        "control, and it is applied before entry.")
