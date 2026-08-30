"""FastAPI routes for the calendar spread strategy."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pcr.config import now_ist
from pcr.kite_client import KiteAuthError, get_session

from . import expiries as expiry_rules
from . import orders as order_svc
from . import store
from .calendar_spread import (DEFAULT_BUY_PREMIUM, DEFAULT_SELL_LOTS,
                              DEFAULT_SELL_PREMIUMS, build_plan, fair_value_zone)
from .chain import build_chain, fetch_spot, pair_by_strike
from .universe import INDICES, expiries_of, get_spec, load_instruments, lot_size

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

router = APIRouter(prefix="/api/strategy", tags=["strategy"])
pages = APIRouter()


class PlanRequest(BaseModel):
    index: str = "NIFTY"
    option_type: str = Field("CE", pattern="^(CE|PE)$")
    buy_premium: float = DEFAULT_BUY_PREMIUM
    sell_premiums: list[float] = Field(default_factory=lambda: list(DEFAULT_SELL_PREMIUMS))
    sell_lots: list[int] = Field(default_factory=lambda: list(DEFAULT_SELL_LOTS))
    weekly_expiry: str | None = None      # override the auto-selected expiry
    monthly_expiry: str | None = None
    max_gap_points: float | None = None
    span_points: float | None = None


class ExecuteRequest(BaseModel):
    plan: dict[str, Any]
    mode: str = Field("paper", pattern="^(paper|live)$")
    confirm: str = ""
    product: str = Field("NRML", pattern="^(NRML|MIS)$")
    order_type: str = Field("LIMIT", pattern="^(LIMIT|MARKET)$")
    note: str = ""


@router.get("/indices")
def list_indices() -> dict[str, Any]:
    return {"indices": [
        {"key": s.key, "label": s.label, "exchange": s.exchange,
         "spot_symbol": s.spot_symbol, "strike_step": s.strike_step,
         "max_gap_pct": s.max_gap_pct}
        for s in INDICES.values()]}


@router.get("/expiries")
def list_expiries(index: str = "NIFTY") -> dict[str, Any]:
    """Available expiries plus the playbook's S.7 auto-selection."""
    spec = get_spec(index)
    session = get_session()
    rows = load_instruments(session, spec)
    all_exp = expiries_of(rows)
    today = now_ist().date()
    choice = expiry_rules.pick(all_exp, today)
    return {
        "index": spec.key,
        "lot_size": lot_size(rows),
        "all": [e.isoformat() for e in all_exp],
        "weeklies": [e.isoformat() for e in expiry_rules.weekly_expiries(all_exp)],
        "monthlies": [e.isoformat() for e in expiry_rules.monthly_expiries(all_exp)],
        "selected": {
            "weekly": choice.weekly.isoformat() if choice.weekly else None,
            "monthly": choice.monthly.isoformat() if choice.monthly else None,
            "usable": choice.usable,
            "reasons": choice.reasons,
        },
    }


@router.get("/chain")
def option_chain(index: str = "NIFTY", weekly: str | None = None,
                 monthly: str | None = None, option_type: str = Query("CE", pattern="^(CE|PE)$"),
                 span_points: float | None = None) -> dict[str, Any]:
    """Weekly-vs-monthly premium table, the S.2 workflow."""
    spec = get_spec(index)
    session = get_session()
    rows = load_instruments(session, spec)
    all_exp = expiries_of(rows)
    choice = expiry_rules.pick(all_exp, now_ist().date())

    w = date.fromisoformat(weekly) if weekly else choice.weekly
    m = date.fromisoformat(monthly) if monthly else choice.monthly
    if w is None or m is None:
        raise HTTPException(400, "No usable weekly/monthly expiry pair. "
                                 + " ".join(choice.reasons))

    spot = fetch_spot(session, spec)
    near = build_chain(session, spec, w, option_type, spot, span_points)
    far = build_chain(session, spec, m, option_type, spot, span_points)
    return {
        "index": spec.key, "option_type": option_type, "spot": round(spot, 2),
        "weekly_expiry": w.isoformat(), "monthly_expiry": m.isoformat(),
        "lot_size": lot_size(rows),
        "table": pair_by_strike(near, far),
        "near": [r.as_row() for r in near],
        "far": [r.as_row() for r in far],
    }


@router.get("/fairvalue")
def fairvalue(index: str = "NIFTY") -> dict[str, Any]:
    """S.8 zone detail: the last crash, its year, today's fair value, and spot."""
    spec = get_spec(index)
    session = get_session()
    spot = fetch_spot(session, spec)

    # Latest top from whatever history Kite will give us (about four years on
    # the daily interval), so it is derived rather than another recorded value.
    top = top_date = None
    try:
        from hilega.data import fetch as fetch_candles
        token = session.quote([spec.spot_symbol])[spec.spot_symbol]["instrument_token"]
        candles = fetch_candles(session, int(token), "day", bars=1200)
        if candles:
            peak = max(candles, key=lambda c: c.high)
            top, top_date = round(peak.high, 2), peak.date[:10]
    except Exception as exc:                       # noqa: BLE001 - optional detail
        log.warning("Could not derive the latest top for %s: %s", spec.key, exc)

    zone = fair_value_zone(spec, spot, now_ist().date(),
                           latest_top=top, latest_top_date=top_date)
    if zone is None:
        raise HTTPException(400, f"No crash anchor is configured for {spec.key}.")
    zone["index"] = spec.key
    zone["label"] = spec.label
    return zone


@router.post("/plan")
def make_plan(req: PlanRequest) -> dict[str, Any]:
    """Build the ready-made 3:1:1 calendar spread and run the entry filters."""
    if len(req.sell_premiums) != len(req.sell_lots):
        raise HTTPException(400, "sell_premiums and sell_lots must be the same length.")
    spec = get_spec(req.index)
    session = get_session()
    rows = load_instruments(session, spec)
    all_exp = expiries_of(rows)
    today = now_ist().date()
    choice = expiry_rules.pick(all_exp, today)

    w = date.fromisoformat(req.weekly_expiry) if req.weekly_expiry else choice.weekly
    m = date.fromisoformat(req.monthly_expiry) if req.monthly_expiry else choice.monthly
    if w is None or m is None:
        raise HTTPException(400, "No usable weekly/monthly expiry pair. "
                                 + " ".join(choice.reasons))
    if w >= m:
        raise HTTPException(400, f"Short leg {w} must expire before the buy leg {m}.")

    spot = fetch_spot(session, spec)
    near = build_chain(session, spec, w, req.option_type, spot, req.span_points)
    far = build_chain(session, spec, m, req.option_type, spot, req.span_points)
    if not near or not far:
        raise HTTPException(502, "Kite returned an empty option chain for one expiry.")

    plan = build_plan(spec, req.option_type, spot, w, m, near, far,
                      buy_premium=req.buy_premium, sell_premiums=req.sell_premiums,
                      sell_lots=req.sell_lots, max_gap_points=req.max_gap_points,
                      on=today)
    out = plan.as_dict()
    out["expiry_reasons"] = choice.reasons
    out["confirmation_phrase"] = order_svc.confirmation_phrase(out)
    out["live_enabled"] = order_svc.live_orders_enabled()
    return out


@router.post("/margin")
def plan_margin(req: ExecuteRequest) -> dict[str, Any]:
    """True basket margin, with the calendar-spread hedge benefit applied."""
    session = get_session()
    try:
        margin = order_svc.basket_margin(session, req.plan, req.product)
    except KiteAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:                       # noqa: BLE001 - surfaced to UI
        raise HTTPException(502, f"Margin lookup failed: {type(exc).__name__}: {exc}") from exc
    margin["available"] = order_svc.available_margin(session)
    if margin["available"] is not None:
        margin["sufficient"] = margin["available"] >= margin["total_required"]
    return margin


@router.post("/execute")
def execute(req: ExecuteRequest) -> dict[str, Any]:
    """Record the basket on paper, or place it live behind both guards."""
    plan = req.plan
    if not plan.get("legs"):
        raise HTTPException(400, "Plan has no legs.")
    store.init_db()

    if req.mode == "paper":
        basket_id = store.save_basket(plan, "paper", "open", note=req.note)
        for leg in plan["legs"]:
            store.save_leg(basket_id, leg, status="paper")
        return {"mode": "paper", "basket_id": basket_id,
                "message": f"Recorded {len(plan['legs'])} legs on paper. "
                           f"Nothing was sent to the exchange."}

    session = get_session()
    try:
        results = order_svc.place_live(session, plan, req.confirm,
                                       req.product, req.order_type)
    except order_svc.LiveOrdersDisabled as exc:
        raise HTTPException(403, str(exc)) from exc
    except order_svc.ConfirmationMismatch as exc:
        raise HTTPException(400, str(exc)) from exc
    except KiteAuthError as exc:
        raise HTTPException(401, str(exc)) from exc

    placed = [r for r in results if r["status"] == "placed"]
    status = ("open" if len(placed) == len(plan["legs"])
              else "partial" if placed else "failed")
    basket_id = store.save_basket(plan, "live", status, note=req.note)
    for r in results:
        store.save_leg(basket_id, r["leg"], status=r["status"],
                       order_id=r.get("order_id"), error=r.get("error"))
    return {
        "mode": "live", "basket_id": basket_id, "status": status,
        "placed": len(placed), "total": len(plan["legs"]),
        "results": [{k: v for k, v in r.items() if k != "leg"} |
                    {"tradingsymbol": r["leg"]["tradingsymbol"],
                     "action": r["leg"]["action"]} for r in results],
        "message": ("All legs placed." if status == "open" else
                    "Placement stopped at a rejection - check the results and your "
                    "Kite orderbook before retrying, you may be holding a partial spread."),
    }


@router.get("/baskets")
def baskets(limit: int = 50, mode: str | None = None) -> dict[str, Any]:
    store.init_db()
    return {"baskets": store.list_baskets(limit=limit, mode=mode)}


@router.get("/status")
def strategy_status() -> dict[str, Any]:
    try:
        session = get_session()
        authed = session.is_authenticated
    except RuntimeError:
        authed = False
    return {
        "authenticated": authed,
        "live_orders_enabled": order_svc.live_orders_enabled(),
        "now_ist": now_ist().isoformat(timespec="seconds"),
    }


@pages.get("/strategy")
def strategy_page() -> FileResponse:
    return FileResponse(STATIC / "strategy.html", headers={"Cache-Control": "no-cache, must-revalidate"})
