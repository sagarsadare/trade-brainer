"""FastAPI surface: JSON series for the chart, plus auth and manual triggers."""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import store
from .backfill import backfill_day, previous_trading_day
from .collector import collect_snapshot
from .config import SETTINGS, is_session_time, now_ist, slots_for
from .kite_client import KiteAuthError, get_session

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

SERIES_KEYS = ("weekly_oi_pcr", "weekly_vol_pcr", "monthly_oi_pcr", "monthly_vol_pcr")

app = FastAPI(title="TradeBrain", version="1.1")

# Calendar spread strategy lives in its own package; mounted here so the
# whole app is one process, one port, one Kite session.
from strategy.api import pages as strategy_pages, router as strategy_router  # noqa: E402
app.include_router(strategy_router)
app.include_router(strategy_pages)
from hilega.api import pages as hilega_pages, router as hilega_router  # noqa: E402
app.include_router(hilega_router)
app.include_router(hilega_pages)

# Shared front-end assets (the page-switcher menu) used by both dashboards.
from pcr.config import ROOT  # noqa: E402
app.mount("/static", StaticFiles(directory=ROOT / "webstatic"), name="static")


def _shape_day(session_date: str) -> dict[str, Any] | None:
    """Pivot stored rows into slot-aligned, null-padded series for Plotly."""
    rows = store.fetch_day(session_date)
    if not rows:
        return None
    slots = slots_for()
    by_key: dict[str, dict[str, float | None]] = {k: {} for k in SERIES_KEYS}
    spot: dict[str, float | None] = {}
    pain: dict[str, dict[str, float | None]] = {"weekly": {}, "monthly": {}}
    expiries: dict[str, str] = {}
    for r in rows:
        kind = r["expiry_kind"]
        by_key[f"{kind}_oi_pcr"][r["slot"]] = r["oi_pcr"]
        by_key[f"{kind}_vol_pcr"][r["slot"]] = r["vol_pcr"]
        expiries[kind] = r["expiry_date"]
        if kind in pain:
            pain[kind][r["slot"]] = r.get("max_pain")
        if r["spot"] is not None:
            spot[r["slot"]] = r["spot"]
    return {
        "date": session_date,
        "expiries": expiries,
        "sources": sorted({r["source"] for r in rows}),
        "series": {k: [by_key[k].get(s) for s in slots] for k in SERIES_KEYS},
        "spot": [spot.get(s) for s in slots],
        "max_pain": {k: [pain[k].get(s) for s in slots] for k in pain},
        "slots_with_strikes": {
            k: store.slots_with_strikes(session_date, k) for k in ("weekly", "monthly")},
        "points": len(rows),
    }


@app.get("/api/series")
def series(day: str | None = Query(None, alias="date"),
           compare: str | None = None) -> dict[str, Any]:
    """PCR curves for one session, optionally overlaid with an earlier one."""
    target = day or (store.available_dates(limit=1) or [now_ist().date().isoformat()])[0]
    primary = _shape_day(target)
    if compare is None:
        compare = store.previous_session_date(target)
    comparison = _shape_day(compare) if compare else None
    return {
        "slots": slots_for(),
        "primary": primary,
        "compare": comparison,
        "requested_date": target,
        "available_dates": store.available_dates(),
    }


@app.get("/api/intraday")
def intraday(day: str | None = Query(None, alias="date"),
             expiry_kind: str = Query("weekly", pattern="^(weekly|monthly)$")) -> dict[str, Any]:
    """The 15-minute intraday trend table: OI both sides, diff, PCR and signals.

    Diff is PUT minus CALL open interest, so it carries the same sign as
    PCR - 1: negative means calls outweigh puts.
    """
    target = day or (store.available_dates(limit=1) or [now_ist().date().isoformat()])[0]
    rows = [r for r in store.fetch_day(target) if r["expiry_kind"] == expiry_kind]
    if not rows:
        raise HTTPException(404, f"No PCR data stored for {target} ({expiry_kind}).")

    vwap_by_slot = _vwap_series(target)
    out = []
    for r in sorted(rows, key=lambda x: x["slot"]):
        pcr = r["oi_pcr"]
        price, vwap = r["spot"], vwap_by_slot.get(r["slot"])
        out.append({
            "slot": r["slot"],
            "call_oi": r["ce_oi"], "put_oi": r["pe_oi"],
            "diff": r["pe_oi"] - r["ce_oi"],
            "pcr": pcr,
            "option_signal": None if pcr is None else ("BUY" if pcr >= 1 else "SELL"),
            "price": price,
            "vwap": vwap,
            "vwap_signal": (None if price is None or vwap is None
                            else ("BUY" if price > vwap else "SELL")),
            "max_pain": r.get("max_pain"),
        })
    return {"date": target, "expiry_kind": expiry_kind,
            "expiry_date": rows[0]["expiry_date"],
            "vwap_source": _VWAP_SOURCE.get(target),
            "rows": out}


_VWAP_SOURCE: dict[str, str | None] = {}


def _vwap_series(session_date: str) -> dict[str, float]:
    """Cumulative session VWAP per slot, from the front-month future.

    The index has no traded volume, so VWAP is taken from the future. Typical
    price is (high + low + close) / 3, accumulated from the session open, and
    each candle is labelled with the slot it *describes* -- the same forward
    shift the backfill uses, so VWAP lines up with the OI columns.
    """
    from datetime import date as _date, datetime as _dt, timedelta
    from .config import IST, SESSION_OPEN
    from .instruments import front_future
    from .pcr import candle_slot

    try:
        session = get_session()
        fut = front_future(session)
        if not fut:
            _VWAP_SOURCE[session_date] = None
            return {}
        on = _date.fromisoformat(session_date)
        start = _dt.combine(on, SESSION_OPEN, tzinfo=IST)
        end = start + timedelta(hours=7)
        candles = session.historical(fut["instrument_token"], start, end, "15minute")
        _VWAP_SOURCE[session_date] = fut["tradingsymbol"]
    except Exception as exc:                       # noqa: BLE001 - VWAP is optional
        log.warning("VWAP unavailable for %s: %s", session_date, exc)
        _VWAP_SOURCE[session_date] = None
        return {}

    pv = vol = 0.0
    out: dict[str, float] = {}
    for c in candles:
        v = float(c.get("volume") or 0)
        typical = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        pv += typical * v
        vol += v
        if vol:
            out[candle_slot(c["date"])] = round(pv / vol, 2)
    return out


@app.get("/api/chain")
def chain(day: str = Query(..., alias="date"), slot: str = Query(...),
          expiry_kind: str = Query("weekly", pattern="^(weekly|monthly)$")) -> dict[str, Any]:
    """Strike-wise PCR curve and the max-pain profile for one stored slot."""
    from .pcr import StrikeRow, max_pain as compute_max_pain

    raw = store.fetch_strikes(day, slot, expiry_kind)
    if not raw:
        raise HTTPException(404, f"No per-strike detail stored for {day} {slot} "
                                 f"{expiry_kind}. Strike detail is recorded from this "
                                 f"version onward - re-run the backfill for that day.")
    rows = [StrikeRow(strike=r["strike"], ce_oi=r["ce_oi"], pe_oi=r["pe_oi"],
                      ce_volume=r["ce_volume"], pe_volume=r["pe_volume"]) for r in raw]
    pain_strike, pain_curve = compute_max_pain(rows)

    snap = [x for x in store.fetch_day(day)
            if x["slot"] == slot and x["expiry_kind"] == expiry_kind]
    return {
        "date": day, "slot": slot, "expiry_kind": expiry_kind,
        "spot": snap[0]["spot"] if snap else None,
        "atm_strike": snap[0]["atm_strike"] if snap else None,
        "expiry_date": snap[0]["expiry_date"] if snap else None,
        "max_pain": pain_strike,
        "strikes": [r.as_row() for r in rows],
        "pain_curve": pain_curve,
    }


@app.get("/api/status")
def status() -> dict[str, Any]:
    try:
        session = get_session()
        authed = session.is_authenticated
        minted = session.token_minted.isoformat(timespec="seconds") if session.token_minted else None
        creds = True
    except RuntimeError as exc:
        authed, minted, creds = False, None, False
        log.warning("Credentials unavailable: %s", exc)
    runs = store.latest_runs(limit=8)
    return {
        "authenticated": authed,
        "credentials_configured": creds,
        "token_minted": minted,
        "now_ist": now_ist().isoformat(timespec="seconds"),
        "market_open": is_session_time(),
        "strike_window": SETTINGS.strike_window,
        "session_close": SETTINGS.session_close.strftime("%H:%M"),
        "available_dates": store.available_dates(limit=30),
        "recent_runs": runs,
        "last_run": runs[0] if runs else None,
    }


@app.get("/auth/kite/login")
def kite_login() -> RedirectResponse:
    return RedirectResponse(get_session().login_url())


@app.get("/auth/kite/callback")
def kite_callback(request_token: str | None = None, status: str | None = None):
    if not request_token:
        log.warning("Callback hit with no request_token (status=%s)", status)
        raise HTTPException(400, f"Kite returned no request_token (status={status}).")
    log.info("Callback received request_token; exchanging for access token.")
    get_session().complete_login(request_token)
    return RedirectResponse("/?connected=1")


MANUAL_PAGE = r"""<!doctype html><meta charset="utf-8">
<title>Connect Kite manually</title>
<style>
 body{background:#0b0f16;color:#e6edf7;font:15px/1.7 "Segoe UI",system-ui,sans-serif;
      display:flex;justify-content:center;padding:48px 20px}
 .card{max-width:660px;width:100%;background:#131a24;border:1px solid #243044;
       border-radius:12px;padding:28px 30px}
 h2{margin:0 0 6px;font-size:19px} p{color:#8b9bb4;margin:10px 0}
 ol{color:#8b9bb4;padding-left:20px} li{margin:9px 0}
 a{color:#60a5fa;word-break:break-all}
 input{width:100%;margin-top:6px;padding:11px 12px;border-radius:7px;
       background:#1a2330;border:1px solid #243044;color:#e6edf7;font:inherit}
 button{margin-top:14px;padding:10px 20px;border-radius:7px;background:#1d4ed8;
        border:1px solid #2563eb;color:#fff;font:inherit;cursor:pointer}
 button:hover{background:#2563eb}
 .err{background:#3b1d1d;border:1px solid #7a2f2f;color:#f87171;
      padding:11px 13px;border-radius:7px;margin-bottom:16px;font-size:14px}
 code{background:#1a2330;padding:2px 6px;border-radius:4px;color:#8b9bb4}
</style>
<div class="card">
 __ERROR__
 <h2>Connect Kite manually</h2>
 <p>Use this when the automatic redirect does not come back to this app.</p>
 <ol>
  <li>Open the Kite login page:<br><a href="__LOGIN_URL__" target="_blank">__LOGIN_URL__</a></li>
  <li>Log in with your Zerodha credentials and 2FA.</li>
  <li>Your browser lands on the redirect URL. <b>It does not matter whether that page
      loads</b> &mdash; even a "site can't be reached" error is fine.</li>
  <li>Copy the <b>entire URL from the address bar</b>. It contains
      <code>request_token=...</code></li>
  <li>Paste it below.</li>
 </ol>
 <form method="post" action="/auth/kite/manual">
  <label>Redirect URL (or just the request_token)
   <input name="pasted" autofocus autocomplete="off"
          placeholder="http://127.0.0.1:8000/auth/kite/callback?request_token=..."></label>
  <button type="submit">Connect</button>
 </form>
</div>"""


def _manual_page(error: str = "") -> HTMLResponse:
    banner = f'<div class="err">{error}</div>' if error else ""
    html = (MANUAL_PAGE
            .replace("__LOGIN_URL__", get_session().login_url())
            .replace("__ERROR__", banner))
    return HTMLResponse(html)


@app.get("/auth/kite/manual")
def kite_manual_form() -> HTMLResponse:
    """Paste-the-URL fallback for when the registered redirect does not reach us."""
    return _manual_page()


@app.post("/auth/kite/manual")
def kite_manual_submit(pasted: str = Form("")):
    from .auth_cli import extract_request_token
    token = extract_request_token(pasted)
    if not token:
        return _manual_page("Nothing pasted.")
    if len(token) > 64 or "/" in token:
        return _manual_page("That does not look like a request_token. Paste the whole "
                            "redirect URL, or just the request_token value from it.")
    try:
        get_session().complete_login(token)
    except Exception as exc:                       # noqa: BLE001 - surfaced to the user
        return _manual_page(f"Kite rejected it: {exc}. Request tokens are single-use "
                            f"and expire within minutes - start again from step 1.")
    return RedirectResponse("/?connected=1", status_code=303)


@app.post("/api/collect")
def collect_now() -> dict[str, Any]:
    """Take a snapshot immediately, independent of the schedule."""
    try:
        points = collect_snapshot()
    except KiteAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"collected": [p.as_row() for p in points]}


@app.post("/api/backfill")
def run_backfill(tasks: BackgroundTasks, day: str | None = Query(None, alias="date")) -> dict[str, Any]:
    """Kick off a historical rebuild; it can take a minute, so it runs detached."""
    target = date.fromisoformat(day) if day else previous_trading_day()
    tasks.add_task(_backfill_guarded, target)
    return {"started": True, "date": target.isoformat(),
            "note": "Poll /api/status; the run appears in recent_runs when finished."}


def _backfill_guarded(target: date) -> None:
    try:
        backfill_day(target)
    except Exception as exc:                       # noqa: BLE001 - background guard
        log.exception("Backfill failed for %s", target)
        store.log_run("backfill", "error", repr(exc), target.isoformat())


@app.get("/")
def dashboard() -> FileResponse:
    # The page is edited while the service runs; without this the browser
    # serves a stale copy from cache after every UI change.
    return FileResponse(STATIC / "dashboard.html", headers={"Cache-Control": "no-cache, must-revalidate"})
