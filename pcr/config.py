"""Central configuration and market-calendar constants for the PCR pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IST = ZoneInfo("Asia/Kolkata")

# Nifty 50 spot, and the exchange we pull the option chain from.
SPOT_SYMBOL = "NSE:NIFTY 50"
OPTION_EXCHANGE = "NFO"
UNDERLYING_NAME = "NIFTY"
STRIKE_STEP = 50.0          # Nifty option strikes are spaced 50 points apart.

SESSION_OPEN = time(9, 15)
SLOT_MINUTES = 15

# Kite published rate limits (req/sec). We stay a hair under them.
RATE_LIMIT_HISTORICAL = 2.5
RATE_LIMIT_QUOTE = 0.9
QUOTE_BATCH_SIZE = 450      # hard API cap is 500 instruments per quote() call.


def _env_time(name: str, default: time) -> time:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    hh, _, mm = raw.partition(":")
    return time(int(hh), int(mm or 0))


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv("KITE_API_KEY", "").strip())
    api_secret: str = field(default_factory=lambda: os.getenv("KITE_API_SECRET", "").strip())
    redirect_url: str = field(
        default_factory=lambda: os.getenv(
            "KITE_REDIRECT_URL", "http://127.0.0.1:8000/auth/kite/callback"
        ).strip()
    )
    strike_window: int = field(default_factory=lambda: int(os.getenv("STRIKE_WINDOW", "10")))
    session_close: time = field(default_factory=lambda: _env_time("SESSION_CLOSE", time(15, 30)))
    db_path: Path = field(
        default_factory=lambda: (ROOT / os.getenv("TRADEBRAIN_DB", "data/pcr.db")).resolve()
    )
    token_path: Path = field(default_factory=lambda: ROOT / "data" / "kite_token.json")
    instruments_cache: Path = field(default_factory=lambda: ROOT / "data" / "nfo_instruments.json")

    def require_credentials(self) -> None:
        missing = [n for n, v in (("KITE_API_KEY", self.api_key),
                                  ("KITE_API_SECRET", self.api_secret)) if not v]
        if missing:
            raise RuntimeError(
                f"Missing {' and '.join(missing)} in {ROOT / '.env'}. "
                "Copy .env.example to .env and fill in your Kite app credentials."
            )


SETTINGS = Settings()


def slots_for(session_close: time | None = None) -> list[str]:
    """Every 15-minute mark of the session as 'HH:MM' IST labels.

    09:15 is the opening print; the last slot is the configured close.
    """
    close = session_close or SETTINGS.session_close
    cursor = datetime.combine(date(2000, 1, 1), SESSION_OPEN)
    end = datetime.combine(date(2000, 1, 1), close)
    out: list[str] = []
    while cursor <= end:
        out.append(cursor.strftime("%H:%M"))
        cursor += timedelta(minutes=SLOT_MINUTES)
    return out


def now_ist() -> datetime:
    return datetime.now(IST)


def slot_label(moment: datetime) -> str:
    """Floor an IST datetime onto its 15-minute slot label."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(IST)
    floored = moment.replace(minute=(moment.minute // SLOT_MINUTES) * SLOT_MINUTES,
                             second=0, microsecond=0)
    return floored.strftime("%H:%M")


def is_session_time(moment: datetime | None = None) -> bool:
    moment = moment or now_ist()
    if moment.weekday() >= 5:          # Sat/Sun. NSE holidays handled by empty data.
        return False
    return SESSION_OPEN <= moment.time() <= SETTINGS.session_close
