"""One-shot diagnostic: what can this Kite API key actually do?

Answers the question the whole backfill design hinges on -- whether the app
has the Historical Data add-on, and specifically whether it returns open
interest on option candles.

    python probe_kite.py
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

from pcr.backfill import previous_trading_day
from pcr.config import IST, SESSION_OPEN, SETTINGS, SPOT_SYMBOL, now_ist
from pcr.instruments import build_chains
from pcr.kite_client import KiteAuthError, get_session

OK, FAIL, WARN = "  [PASS]", "  [FAIL]", "  [WARN]"
results: dict[str, bool] = {}


def check(name: str, fn):
    print(f"\n{name}")
    try:
        detail = fn()
        results[name] = True
        print(f"{OK} {detail}")
        return True
    except Exception as exc:                       # noqa: BLE001 - diagnostic
        results[name] = False
        print(f"{FAIL} {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    print("=" * 72)
    print("Kite Connect capability probe - TradeBrain PCR pipeline")
    print("=" * 72)

    try:
        session = get_session()
    except RuntimeError as exc:
        print(f"\n{FAIL} {exc}")
        return 1

    if not session.is_authenticated:
        print("\nNo valid access token. Run:  python -m pcr.auth_cli")
        return 1

    check("1. Profile / session", lambda: f"logged in as {session.profile()['user_name']}")

    state: dict = {}

    def load_chains():
        chains = build_chains(session)
        state["chains"] = chains
        return ", ".join(f"{k} expiry {c.expiry} ({len(c.strikes)} strikes)"
                         for k, c in chains.items())

    check("2. NFO instrument dump + expiry resolution", load_chains)

    def spot():
        q = session.quote([SPOT_SYMBOL])[SPOT_SYMBOL]
        state["spot"] = float(q["last_price"])
        state["index_token"] = int(q["instrument_token"])
        return f"{SPOT_SYMBOL} = {state['spot']:.2f} (token {state['index_token']})"

    check("3. Live index quote", spot)

    def option_quote():
        chain = state["chains"]["weekly"]
        legs = chain.window(state["spot"], SETTINGS.strike_window)
        state["legs"] = legs
        payload = session.quote([l.symbol for l in legs])
        sample = next(iter(payload.values()))
        have_oi = "oi" in sample
        have_vol = any(k in sample for k in ("volume", "volume_traded"))
        return (f"{len(payload)}/{len(legs)} legs quoted; "
                f"oi field={have_oi}, volume field={have_vol}")

    check("4. Live option-chain quote (OI + volume)", option_quote)

    prev = previous_trading_day()
    start = datetime.combine(prev, SESSION_OPEN, tzinfo=IST)
    end = start + timedelta(hours=6, minutes=30)

    def hist_index():
        c = session.historical(state["index_token"], start, end, "15minute", oi=False)
        return f"{len(c)} 15-min index candles for {prev}"

    check("5. Historical API - index candles", hist_index)

    def hist_option_oi():
        leg = state["legs"][len(state["legs"]) // 2]
        c = session.historical(leg.token, start, end, "15minute", oi=True)
        if not c:
            raise RuntimeError(f"no candles returned for {leg.tradingsymbol} on {prev}")
        with_oi = sum(1 for x in c if x.get("oi"))
        if not with_oi:
            raise RuntimeError("candles returned but every 'oi' value is empty/zero")
        return (f"{len(c)} candles for {leg.tradingsymbol}, {with_oi} carry OI "
                f"(first oi={c[0].get('oi')}, last oi={c[-1].get('oi')})")

    check("6. Historical API - OPTION candles WITH open interest", hist_option_oi)

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    live_ok = results.get("4. Live option-chain quote (OI + volume)", False)
    hist_ok = results.get("6. Historical API - OPTION candles WITH open interest", False)

    if live_ok:
        print("  Live 15-min PCR collection: SUPPORTED. Start the service now.")
    else:
        print("  Live 15-min PCR collection: BLOCKED - fix step 4 above first.")

    if hist_ok:
        print("  Previous-day 15-min PCR backfill: SUPPORTED.")
        print("  You have the Historical Data add-on. Run:  python run.py backfill --days 5")
    else:
        print("  Previous-day 15-min PCR backfill: NOT AVAILABLE.")
        print("  Your key lacks the Historical Data add-on (Rs.2000/mo at")
        print("  https://developers.kite.trade/apps), or the option had no trades that day.")
        print("  Without it, 'yesterday' can only come from days the live collector ran,")
        print("  so the comparison line will appear from your second session onward.")
    print()
    return 0 if live_ok else 1


if __name__ == "__main__":
    sys.exit(main())
