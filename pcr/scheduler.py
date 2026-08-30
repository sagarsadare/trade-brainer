"""APScheduler wiring for the 15-minute cadence."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .backfill import backfill_previous_day
from .config import IST, SESSION_OPEN, SETTINGS
from .store import log_run

log = logging.getLogger(__name__)


def build_scheduler() -> BackgroundScheduler:
    """Fire 20s after each 15-minute mark, 09:15-close IST, Mon-Fri.

    The 20-second offset lets the exchange settle the boundary bar so the OI
    we read is the state *at* the mark, matching how backfilled candles are
    interpreted.
    """
    from .collector import safe_collect

    scheduler = BackgroundScheduler(timezone=IST)
    close = SETTINGS.session_close
    hours = f"{SESSION_OPEN.hour}-{close.hour}"

    scheduler.add_job(
        safe_collect, CronTrigger(day_of_week="mon-fri", hour=hours,
                                  minute="0,15,30,45", second=20, timezone=IST),
        id="pcr_live", name="Live 15-min PCR snapshot",
        max_instances=1, coalesce=True, misfire_grace_time=120,
    )

    # Rebuild the previous session once, after the overnight token refresh
    # window, so "yesterday" is complete before the new day's first tick.
    scheduler.add_job(
        _morning_backfill, CronTrigger(day_of_week="mon-fri", hour=8, minute=45, timezone=IST),
        id="pcr_backfill", name="Previous-session backfill",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    return scheduler


def _morning_backfill() -> None:
    try:
        points = backfill_previous_day()
        log.info("Morning backfill stored %d points", len(points))
    except Exception as exc:                       # noqa: BLE001 - scheduler guard
        log.exception("Morning backfill failed")
        log_run("backfill", "error", repr(exc))
