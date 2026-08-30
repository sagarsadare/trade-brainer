"""Seed the DB with synthetic but plausible PCR walks, for UI verification.

    python tools/seed_demo.py
    python tools/seed_demo.py --clear

Nothing here touches Kite. Rows are tagged source='demo' so they are easy to
identify and delete once real collection starts.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pcr.config import IST, slots_for                      # noqa: E402
from pcr.pcr import ratio                                  # noqa: E402
from pcr.store import connection, init_db, upsert_snapshots  # noqa: E402


def synth_day(day: date, seed: int, spot0: float) -> list[dict]:
    rng = random.Random(seed)
    slots = slots_for()
    rows, spot = [], spot0
    state = {"weekly": 0.95 + rng.random() * 0.25, "monthly": 1.00 + rng.random() * 0.2}
    for i, slot in enumerate(slots):
        spot += rng.gauss(0, 22) + 6 * math.sin(i / 4.0)
        atm = round(spot / 50) * 50
        for kind, expiry in (("weekly", day + timedelta(days=(1 - day.weekday()) % 7 or 7)),
                             ("monthly", date(day.year, day.month, 28))):
            # Mean-reverting drift with a mild inverse tilt against spot moves.
            state[kind] += rng.gauss(0, 0.018) - 0.05 * (state[kind] - 1.05)
            oi_pcr = max(0.45, state[kind])
            base_ce = 1_800_000 + i * 22_000 + rng.randint(-60_000, 60_000)
            ce_oi = int(base_ce * (1.0 if kind == "weekly" else 0.55))
            pe_oi = int(ce_oi * oi_pcr)
            # Volume PCR is noisier and cumulative through the day.
            vol_scale = (i + 1) / len(slots)
            ce_vol = int(ce_oi * 1.7 * vol_scale * (0.9 + rng.random() * 0.2))
            pe_vol = int(ce_vol * max(0.4, oi_pcr + rng.gauss(0, 0.09)))
            rows.append(dict(
                session_date=day.isoformat(), slot=slot, expiry_kind=kind,
                expiry_date=expiry.isoformat(),
                captured_at=datetime.combine(day, datetime.min.time(), tzinfo=IST)
                    .replace(hour=int(slot[:2]), minute=int(slot[3:])).isoformat(timespec="seconds"),
                spot=round(spot, 2), atm_strike=float(atm), n_strikes=21,
                ce_oi=ce_oi, pe_oi=pe_oi, ce_volume=ce_vol, pe_volume=pe_vol,
                oi_pcr=ratio(pe_oi, ce_oi), vol_pcr=ratio(pe_vol, ce_vol),
                source="demo"))
    return rows


def clear_demo() -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM pcr_snapshot WHERE source = 'demo'")
        conn.execute("DELETE FROM collection_log WHERE detail LIKE 'demo%'")
        return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="remove demo rows and exit")
    ap.add_argument("--days", type=int, default=3)
    args = ap.parse_args()

    init_db()
    if args.clear:
        print(f"Removed {clear_demo()} demo rows.")
        return 0

    from pcr.backfill import previous_trading_day
    days, cursor = [], None
    for _ in range(args.days):
        cursor = previous_trading_day(cursor)
        days.append(cursor)
    days.sort()

    total = 0
    spot = 24_600.0
    for n, day in enumerate(days):
        rows = synth_day(day, seed=1000 + n, spot0=spot)
        spot = rows[-1]["spot"]
        total += upsert_snapshots(rows)
        print(f"{day}: {len(rows)} demo rows")
    from pcr.store import log_run
    log_run("backfill", "ok", f"demo seed: {total} rows", days[-1].isoformat())
    print(f"\nSeeded {total} rows across {len(days)} sessions. "
          f"Run `python run.py serve` and open http://127.0.0.1:8000/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
