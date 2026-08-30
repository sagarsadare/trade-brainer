"""FastAPI routes for the Hilega Milega strategy."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pcr.config import now_ist
from pcr.kite_client import KiteAuthError, get_session
from strategy import orders as order_svc

from . import backtest as bt
from . import store
from .data import TIMEFRAMES, fetch, higher_timeframe
from .indicators import HM
from .strategy import (DEFAULT_MIN_RR, DEFAULT_NO_TRADE_LOOKBACK,
                       DEFAULT_RISK_PCT, detect, evaluate)

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

router = APIRouter(prefix="/api/hilega", tags=["hilega"])
pages = APIRouter()

PRESETS = [
    {"symbol": "NSE:NIFTY 50", "label": "Nifty 50 (index)", "lot_size": 65, "tradable": False},
    {"symbol": "NSE:NIFTY BANK", "label": "Bank Nifty (index)", "lot_size": 30, "tradable": False},
    {"symbol": "BSE:SENSEX", "label": "Sensex (index)", "lot_size": 20, "tradable": False},
    {"symbol": "NSE:RELIANCE", "label": "Reliance", "lot_size": 1, "tradable": True},
    {"symbol": "NSE:HDFCBANK", "label": "HDFC Bank", "lot_size": 1, "tradable": True},
    {"symbol": "NSE:INFY", "label": "Infosys", "lot_size": 1, "tradable": True},
]


class SignalRequest(BaseModel):
    symbol: str = "NSE:NIFTY 50"
    interval: str = "day"
    bars: int = 500
    capital: float = 1_000_000.0
    risk_pct: float = DEFAULT_RISK_PCT
    min_rr: float = DEFAULT_MIN_RR
    lot_size: int = 1
    rsi_length: int = 9
    ema_length: int = 3
    wma_length: int = 21
    no_trade_lookback: int = DEFAULT_NO_TRADE_LOOKBACK
    no_trade_threshold: float | None = None     # None -> calibrate from history
    no_trade_mid_band: float | None = None
    calibrate_percentile: float = 0.25


class ExecuteRequest(BaseModel):
    signal: dict[str, Any]
    context: dict[str, Any]
    mode: str = Field("paper", pattern="^(paper|live)$")
    confirm: str = ""
    tradingsymbol: str = ""
    exchange: str = "NSE"
    product: str = Field("MIS", pattern="^(NRML|MIS|CNC)$")
    note: str = ""


def _resolve(symbol: str) -> tuple[int, float]:
    """Instrument token and last price for a Kite `EXCHANGE:SYMBOL` string."""
    session = get_session()
    payload = session.quote([symbol])
    entry = payload.get(symbol)
    if not entry:
        raise HTTPException(400, f"Kite returned no quote for '{symbol}'. Use the "
                                 f"EXCHANGE:TRADINGSYMBOL form, e.g. NSE:RELIANCE.")
    return int(entry["instrument_token"]), float(entry["last_price"])


def _series(req: SignalRequest) -> dict[str, Any]:
    session = get_session()
    token, ltp = _resolve(req.symbol)
    hi_tf = higher_timeframe(req.interval)
    candles = fetch(session, token, req.interval, bars=req.bars)
    higher = fetch(session, token, hi_tf, bars=max(80, req.bars // 4))
    if len(candles) < 40:
        raise HTTPException(502, f"Only {len(candles)} candles returned for "
                                 f"{req.symbol} {req.interval}; need at least 40.")
    return {"token": token, "ltp": ltp, "candles": candles, "higher": higher,
            "higher_interval": hi_tf}


def _markers(candles, higher, hm, req: "SignalRequest", threshold: float,
             mid_band: float, tail: int) -> list[dict[str, Any]]:
    """Historical buy / sell / exit points to plot on the chart.

    Entries and exits come from the backtest, so what is drawn is exactly what
    the full rule stack would have taken -- not raw crossovers. Raw S.3c
    triggers that every gate rejected are marked separately, because seeing
    what was skipped is as informative as seeing what was taken.
    """
    res = bt.run(candles, higher, req.symbol, req.interval, "",
                 capital=req.capital, risk_pct=req.risk_pct, min_rr=req.min_rr,
                 lot_size=req.lot_size, rsi_length=req.rsi_length,
                 ema_length=req.ema_length, wma_length=req.wma_length,
                 no_trade_lookback=req.no_trade_lookback,
                 no_trade_threshold=threshold, no_trade_mid_band=mid_band)

    by_date = {c.date: n for n, c in enumerate(candles)}
    taken: set[str] = set()
    out: list[dict[str, Any]] = []

    for t in res.trades:
        taken.add(t.entry_date)
        for stamp, kind, price, label in (
            (t.entry_date, "BUY" if t.side == "LONG" else "SELL", t.entry,
             f"{t.side} entry, stop {t.stop_loss:.2f}, target {t.target:.2f}"),
            (t.exit_date, "EXIT", t.exit,
             f"Exit ({t.exit_reason}) {t.r_multiple:+.2f}R after {t.bars_held} bars"),
        ):
            n = by_date.get(stamp)
            if n is None:
                continue
            out.append({"date": stamp, "kind": kind, "price": round(price, 2),
                        "rsi": None if hm.rsi[n] is None else round(hm.rsi[n], 2),
                        "label": label})

    for n in range(1, len(candles)):
        if not (hm.ready(n) and hm.ready(n - 1)):
            continue
        raw, _ = detect(candles, hm, n)
        if raw != "HOLD" and candles[n].date not in taken:
            out.append({"date": candles[n].date, "kind": "BLOCKED",
                        "price": round(candles[n].close, 2),
                        "rsi": round(hm.rsi[n], 2),
                        "label": f"{raw} setup blocked by a gate"})

    first = candles[-tail].date if len(candles) > tail else candles[0].date
    return sorted([m for m in out if m["date"] >= first], key=lambda m: m["date"])


@router.get("/meta")
def meta() -> dict[str, Any]:
    try:
        authed = get_session().is_authenticated
    except RuntimeError:
        authed = False
    return {
        "authenticated": authed,
        "live_orders_enabled": order_svc.live_orders_enabled(),
        "now_ist": now_ist().isoformat(timespec="seconds"),
        "presets": PRESETS,
        "timeframes": [{"key": k, "label": v["label"], "higher": v["higher"]}
                       for k, v in TIMEFRAMES.items() if k != "month"],
    }


@router.post("/signal")
def signal(req: SignalRequest) -> dict[str, Any]:
    """Current Hilega Milega state, the full gate stack, and the resulting call."""
    data = _series(req)
    candles, higher = data["candles"], data["higher"]

    cal = bt.calibrate_thresholds(candles, req.no_trade_lookback,
                                  req.calibrate_percentile, req.rsi_length,
                                  req.ema_length, req.wma_length)
    threshold = req.no_trade_threshold if req.no_trade_threshold is not None \
        else cal.get("no_trade_threshold", 6.0)
    mid_band = req.no_trade_mid_band if req.no_trade_mid_band is not None \
        else cal.get("no_trade_mid_band", 8.0)

    sig = evaluate(candles, higher, rsi_length=req.rsi_length,
                   ema_length=req.ema_length, wma_length=req.wma_length,
                   no_trade_lookback=req.no_trade_lookback,
                   no_trade_threshold=threshold, no_trade_mid_band=mid_band,
                   capital=req.capital, risk_pct=req.risk_pct, min_rr=req.min_rr,
                   lot_size=req.lot_size)

    hm = HM([c.close for c in candles], req.rsi_length, req.ema_length, req.wma_length)
    hm_hi = HM([c.close for c in higher], req.rsi_length, req.ema_length, req.wma_length)
    tail = 180
    out = sig.as_dict()
    out.update({
        "symbol": req.symbol, "interval": req.interval,
        "higher_interval": data["higher_interval"], "ltp": round(data["ltp"], 2),
        "calibration": cal,
        "thresholds_used": {"no_trade_threshold": threshold,
                            "no_trade_mid_band": mid_band,
                            "source": "request" if req.no_trade_threshold is not None
                                      else "calibrated from this instrument's history"},
        "higher_state": {
            "bias": sig.trend_bias,
            "rsi": round(hm_hi.rsi[hm_hi.last_ready_index()], 2)
                   if hm_hi.last_ready_index() is not None else None,
            "bars": len(higher),
            "last_bar": higher[-1].date if higher else None,
        },
        "series": {
            "dates": [c.date for c in candles[-tail:]],
            "close": [c.close for c in candles[-tail:]],
            "rsi": [None if v is None else round(v, 2) for v in hm.rsi[-tail:]],
            "green": [None if v is None else round(v, 2) for v in hm.green[-tail:]],
            "red": [None if v is None else round(v, 2) for v in hm.red[-tail:]],
        },
        "markers": _markers(candles, higher, hm, req, threshold, mid_band, tail),
        "confirmation_phrase": f"PLACE HM {sig.signal} {req.symbol.split(':')[-1]}",
        "live_enabled": order_svc.live_orders_enabled(),
    })
    return out


@router.post("/backtest")
def backtest(req: SignalRequest) -> dict[str, Any]:
    """Walk the history bar by bar under the full rule stack (rules S.9)."""
    data = _series(req)
    candles, higher = data["candles"], data["higher"]
    cal = bt.calibrate_thresholds(candles, req.no_trade_lookback,
                                  req.calibrate_percentile, req.rsi_length,
                                  req.ema_length, req.wma_length)
    threshold = req.no_trade_threshold if req.no_trade_threshold is not None \
        else cal.get("no_trade_threshold", 6.0)
    mid_band = req.no_trade_mid_band if req.no_trade_mid_band is not None \
        else cal.get("no_trade_mid_band", 8.0)

    res = bt.run(candles, higher, req.symbol, req.interval, data["higher_interval"],
                 capital=req.capital, risk_pct=req.risk_pct, min_rr=req.min_rr,
                 lot_size=req.lot_size, rsi_length=req.rsi_length,
                 ema_length=req.ema_length, wma_length=req.wma_length,
                 no_trade_lookback=req.no_trade_lookback,
                 no_trade_threshold=threshold, no_trade_mid_band=mid_band)
    out = res.as_dict()
    out["calibration"] = cal
    st = out["stats"]
    if st.get("trades", 0) < 30:
        out["caveat"] = (f"Only {st.get('trades', 0)} trades in this window. That is far "
                         f"too small a sample to judge an edge -- treat the win rate and "
                         f"expectancy as noise, not evidence. Widen the history or the "
                         f"timeframe before drawing any conclusion.")
    return out


@router.post("/execute")
def execute(req: ExecuteRequest) -> dict[str, Any]:
    """Record on paper, or place entry + SL-M live behind the same two guards."""
    sig, ctx = req.signal, req.context
    if sig.get("signal") not in ("LONG", "SHORT"):
        raise HTTPException(400, "Signal is HOLD - there is nothing to place.")
    if not sig.get("quantity"):
        raise HTTPException(400, "Signal has no position size; it cannot be placed.")
    store.init_db()

    tradingsymbol = req.tradingsymbol or ctx.get("symbol", "").split(":")[-1]
    ctx = {**ctx, "tradingsymbol": tradingsymbol, "exchange": req.exchange}

    if store.already_recorded(req.mode, tradingsymbol, ctx.get("interval", ""),
                              sig["bar_date"], sig["signal"]):
        raise HTTPException(409, f"A {req.mode} {sig['signal']} for {tradingsymbol} on "
                                 f"bar {sig['bar_date']} is already recorded. The same "
                                 f"bar's signal is never fired twice.")

    if req.mode == "paper":
        tid = store.save_trade(sig, ctx, "paper", "open", note=req.note)
        return {"mode": "paper", "trade_id": tid,
                "message": f"Recorded {sig['signal']} {sig['quantity']} {tradingsymbol} "
                           f"on paper. Nothing was sent to the exchange."}

    expected = f"PLACE HM {sig['signal']} {tradingsymbol}"
    if not order_svc.live_orders_enabled():
        raise HTTPException(403, "Live orders are disabled. Set ALLOW_LIVE_ORDERS=true "
                                 "in .env and restart the service.")
    if (req.confirm or "").strip() != expected:
        raise HTTPException(400, f'Confirmation must be exactly "{expected}".')

    session = get_session()
    session.require_auth()
    side = "BUY" if sig["signal"] == "LONG" else "SELL"
    opposite = "SELL" if side == "BUY" else "BUY"
    entry_id = sl_id = None
    try:
        entry_id = session.kite.place_order(
            variety="regular", exchange=req.exchange, tradingsymbol=tradingsymbol,
            transaction_type=side, quantity=sig["quantity"], product=req.product,
            order_type="MARKET", tag="HM_ENTRY")
        # The protective stop goes in immediately after the fill, never later.
        sl_id = session.kite.place_order(
            variety="regular", exchange=req.exchange, tradingsymbol=tradingsymbol,
            transaction_type=opposite, quantity=sig["quantity"], product=req.product,
            order_type="SL-M", trigger_price=sig["stop_loss"], tag="HM_SL")
        status = "open"
        message = "Entry and stop-loss placed."
    except KiteAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:                       # noqa: BLE001 - reported to the UI
        log.exception("HM live placement failed")
        status = "partial" if entry_id else "failed"
        message = (f"{type(exc).__name__}: {exc}. "
                   + ("THE ENTRY WENT THROUGH BUT THE STOP DID NOT - you are holding an "
                      "unprotected position. Check your Kite orderbook now."
                      if entry_id else "No orders were placed."))
        tid = store.save_trade(sig, ctx, "live", status, entry_id, sl_id,
                               error=str(exc), note=req.note)
        return {"mode": "live", "trade_id": tid, "status": status,
                "entry_order_id": entry_id, "sl_order_id": sl_id, "message": message}

    tid = store.save_trade(sig, ctx, "live", status, entry_id, sl_id, note=req.note)
    return {"mode": "live", "trade_id": tid, "status": status,
            "entry_order_id": entry_id, "sl_order_id": sl_id, "message": message}


@router.get("/trades")
def trades(limit: int = 50) -> dict[str, Any]:
    store.init_db()
    return {"trades": store.list_trades(limit)}


@pages.get("/hilega")
def hilega_page() -> FileResponse:
    return FileResponse(STATIC / "hilega.html",
                        headers={"Cache-Control": "no-cache, must-revalidate"})
