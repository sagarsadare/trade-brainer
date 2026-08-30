"""TradeBrain PCR - single entry point.

    python run.py serve                 # dashboard + 15-min scheduler (the normal way to run)
    python run.py collect               # one snapshot right now
    python run.py backfill              # rebuild the previous trading session
    python run.py backfill --date 2026-08-28
    python run.py backfill --days 5     # rebuild the last 5 trading sessions
    python run.py login                 # interactive Kite login
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"


def setup_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(LOG_DIR / "tradebrain.log", encoding="utf-8")],
    )
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)


def cmd_serve(args) -> int:
    import uvicorn
    from pcr.api import app
    from pcr.scheduler import build_scheduler
    from pcr.store import init_db
    from strategy.store import init_db as init_strategy_db

    init_db()
    init_strategy_db()
    from hilega.store import init_db as init_hilega_db
    init_hilega_db()
    scheduler = build_scheduler()
    scheduler.start()
    log = logging.getLogger("run")
    for job in scheduler.get_jobs():
        log.info("Scheduled %-14s next run: %s", job.id, job.next_run_time)
    log.info("Dashboard -> http://127.0.0.1:%d/", args.port)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        scheduler.shutdown(wait=False)
    return 0


def cmd_collect(args) -> int:
    from pcr.collector import collect_snapshot
    from pcr.store import init_db
    init_db()
    for p in collect_snapshot():
        print(f"{p.session_date} {p.slot} {p.expiry_kind:<7} exp={p.expiry_date} "
              f"spot={p.spot} atm={p.atm_strike} strikes={p.n_strikes} "
              f"oi_pcr={p.oi_pcr} vol_pcr={p.vol_pcr}")
    return 0


def _trading_days_back(n: int) -> list[date]:
    from pcr.backfill import previous_trading_day
    days, cursor = [], None
    for _ in range(n):
        cursor = previous_trading_day(cursor)
        days.append(cursor)
    return sorted(days)


def cmd_backfill(args) -> int:
    from pcr.backfill import backfill_day, previous_trading_day
    from pcr.store import init_db
    init_db()

    if args.date:
        targets = [date.fromisoformat(args.date)]
    elif args.days > 1:
        targets = _trading_days_back(args.days)
    else:
        targets = [previous_trading_day()]

    total, filled = 0, 0
    for day in targets:
        points = backfill_day(day, allow_stale_expiry=args.allow_stale_expiry)
        total += len(points)
        filled += 1 if points else 0
        if not points:
            print(f"{day}: no data (holiday, weekend, or historical access unavailable)")
            continue
        for kind in ("weekly", "monthly"):
            sub = [p for p in points if p.expiry_kind == kind]
            if sub:
                print(f"{day} {kind:<7}: {len(sub):2d} slots, "
                      f"oi_pcr {sub[0].oi_pcr} -> {sub[-1].oi_pcr}, "
                      f"vol_pcr {sub[0].vol_pcr} -> {sub[-1].vol_pcr}")
    print(f"\nStored {total} PCR points across {len(targets)} session(s).")
    return 0


def cmd_login(args) -> int:
    from pcr.auth_cli import extract_request_token, main as auth_main
    if not args.token:
        return auth_main()
    from pcr.kite_client import get_session
    token = extract_request_token(args.token)
    if not token:
        print("Could not find a request_token in that value.")
        return 1
    session = get_session()
    data = session.complete_login(token)
    print(f"Logged in as {data.get('user_name')} ({data.get('user_id')}).")
    print(f"Token cached at {session.settings.token_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="dashboard + 15-minute scheduler")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("collect", help="take one snapshot now")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("backfill", help="rebuild past sessions from historical OI")
    p.add_argument("--date", help="YYYY-MM-DD; defaults to the previous trading day")
    p.add_argument("--days", type=int, default=1, help="rebuild the last N trading days")
    p.add_argument("--allow-stale-expiry", action="store_true",
                   help="backfill even when the session's front contract has delisted "
                        "(the series will not be comparable across days)")
    p.set_defaults(fn=cmd_backfill)

    p = sub.add_parser("login", help="Kite login")
    p.add_argument("--token", help="paste the redirect URL (or bare request_token) "
                                   "to skip the interactive prompt")
    p.set_defaults(fn=cmd_login)

    args = parser.parse_args()
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
