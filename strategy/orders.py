"""Order construction, margin lookup, and paper/live execution.

Safety posture, in order of strictness:

1. Paper is the default mode. Nothing reaches the exchange.
2. Live placement is refused outright unless ALLOW_LIVE_ORDERS=true in .env.
3. Even when enabled, the caller must echo an exact confirmation phrase that
   names the index and leg count, so a stray POST cannot fire a basket.
4. Long legs are placed before short legs, so the exchange grants the spread
   margin benefit rather than blocking the shorts for want of margin.

There is no auto-trading path anywhere in this module: every live placement
originates from an explicit, confirmed user action.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Sequence

from pcr.kite_client import KiteSession

log = logging.getLogger(__name__)

CONFIRM_TEMPLATE = "PLACE {n} {index} LIVE"


def live_orders_enabled() -> bool:
    return os.getenv("ALLOW_LIVE_ORDERS", "").strip().lower() in ("1", "true", "yes")


def confirmation_phrase(plan: dict[str, Any]) -> str:
    return CONFIRM_TEMPLATE.format(n=len(plan["legs"]), index=plan["index"])


def to_order_params(leg: dict[str, Any], product: str = "NRML",
                    order_type: str = "LIMIT",
                    price: float | None = None) -> dict[str, Any]:
    """One Kite order payload for one leg.

    LIMIT is the default deliberately: these are multi-leg option orders where
    a MARKET fill on an illiquid strike can slip badly.
    """
    params: dict[str, Any] = {
        "variety": "regular",
        "exchange": leg["exchange"],
        "tradingsymbol": leg["tradingsymbol"],
        "transaction_type": leg["action"],
        "quantity": leg["quantity"],
        "product": product,
        "order_type": order_type,
        "validity": "DAY",
    }
    if order_type == "LIMIT":
        params["price"] = float(price if price is not None else leg["premium"])
    return params


def build_basket(plan: dict[str, Any], product: str = "NRML",
                 order_type: str = "LIMIT") -> list[dict[str, Any]]:
    """Order payloads for the whole plan, long legs first (margin benefit)."""
    legs = sorted(plan["legs"], key=lambda l: 0 if l["action"] == "BUY" else 1)
    return [to_order_params(l, product, order_type) for l in legs]


def basket_margin(session: KiteSession, plan: dict[str, Any],
                  product: str = "NRML") -> dict[str, Any]:
    """True SPAN+exposure requirement for the basket, with spread benefit.

    Uses Kite's basket endpoint rather than summing per-leg margins, because
    the whole point of a calendar spread is that the hedge reduces the
    requirement -- summing legs would overstate it badly.
    """
    session.require_auth()
    params = build_basket(plan, product, order_type="MARKET")
    raw = session.kite.basket_order_margins(params, consider_positions=True)
    initial = (raw or {}).get("initial") or {}
    final = (raw or {}).get("final") or {}
    return {
        "total_without_benefit": round(float(initial.get("total") or 0.0), 2),
        "total_required": round(float(final.get("total") or 0.0), 2),
        "span": round(float(final.get("span") or 0.0), 2),
        "exposure": round(float(final.get("exposure") or 0.0), 2),
        "option_premium": round(float(final.get("option_premium") or 0.0), 2),
        "raw": raw,
    }


def available_margin(session: KiteSession) -> float | None:
    try:
        m = session.kite.margins(segment="equity")
        return round(float(m["net"]), 2)
    except Exception as exc:                       # noqa: BLE001 - informational
        log.warning("Could not read available margin: %s", exc)
        return None


class LiveOrdersDisabled(RuntimeError):
    pass


class ConfirmationMismatch(RuntimeError):
    pass


def place_live(session: KiteSession, plan: dict[str, Any], confirm: str,
               product: str = "NRML", order_type: str = "LIMIT",
               tag: str = "calspread") -> list[dict[str, Any]]:
    """Place the basket on Kite. Guarded, sequential, and never automatic.

    Returns one result per leg. Stops at the first rejection rather than
    leaving a half-built spread growing: an unhedged short leg is the one
    outcome worth failing loudly on.
    """
    if not live_orders_enabled():
        raise LiveOrdersDisabled(
            "Live orders are disabled. Set ALLOW_LIVE_ORDERS=true in .env and "
            "restart the service to enable them.")
    expected = confirmation_phrase(plan)
    if (confirm or "").strip() != expected:
        raise ConfirmationMismatch(f'Confirmation must be exactly "{expected}".')

    session.require_auth()
    legs = sorted(plan["legs"], key=lambda l: 0 if l["action"] == "BUY" else 1)
    results: list[dict[str, Any]] = []
    for leg in legs:
        params = to_order_params(leg, product, order_type)
        try:
            order_id = session.kite.place_order(tag=tag, **params)
            log.info("Placed %s %s x%d -> order %s",
                     leg["action"], leg["tradingsymbol"], leg["quantity"], order_id)
            results.append({"leg": leg, "order_id": order_id, "status": "placed"})
        except Exception as exc:                   # noqa: BLE001 - reported to caller
            log.exception("Order REJECTED for %s", leg["tradingsymbol"])
            results.append({"leg": leg, "order_id": None, "status": "rejected",
                            "error": f"{type(exc).__name__}: {exc}"})
            break
    return results
