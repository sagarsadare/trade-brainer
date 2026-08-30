"""Kite Connect session management, token caching and rate-limited calls.

Kite access tokens are valid for a single trading day (they expire around
06:00 IST the next morning), so the token is cached on disk with the date it
was minted for and silently reused until it goes stale.
"""
from __future__ import annotations

import json
import logging
import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Sequence

from kiteconnect import KiteConnect
from kiteconnect.exceptions import DataException, NetworkException, TokenException

from .config import (IST, QUOTE_BATCH_SIZE, RATE_LIMIT_HISTORICAL, RATE_LIMIT_QUOTE,
                     SETTINGS, now_ist)

log = logging.getLogger(__name__)


class KiteAuthError(RuntimeError):
    """Raised when no usable access token is available."""


class RateLimiter:
    """Minimum-interval throttle, safe across threads."""

    def __init__(self, per_second: float):
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = _time.monotonic()
            if now < self._next_at:
                _time.sleep(self._next_at - now)
                now = _time.monotonic()
            self._next_at = now + self._interval


def _token_valid_for(minted_at: datetime, moment: datetime) -> bool:
    """A token minted on day D is good until 06:00 IST on the next calendar day."""
    expiry = datetime.combine(minted_at.date() + timedelta(days=1),
                              datetime.min.time(), tzinfo=IST).replace(hour=6)
    return moment < expiry


class KiteSession:
    """Owns one authenticated KiteConnect handle plus per-endpoint throttles."""

    def __init__(self, settings=SETTINGS):
        self.settings = settings
        settings.require_credentials()
        self.kite = KiteConnect(api_key=settings.api_key)
        self._hist_limiter = RateLimiter(RATE_LIMIT_HISTORICAL)
        self._quote_limiter = RateLimiter(RATE_LIMIT_QUOTE)
        self._lock = threading.Lock()
        self.access_token: str | None = None
        self.token_minted: datetime | None = None
        self._load_cached_token()

    # ---------------------------------------------------------------- auth --
    def _load_cached_token(self) -> None:
        path = self.settings.token_path
        if not path.exists():
            return
        try:
            blob = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Ignoring unreadable token cache at %s", path)
            return
        if blob.get("api_key") != self.settings.api_key:
            return  # token belongs to a different app
        minted = datetime.fromisoformat(blob["minted_at"])
        if not _token_valid_for(minted, now_ist()):
            log.info("Cached Kite token expired (minted %s); login required.", minted)
            return
        self.access_token = blob["access_token"]
        self.token_minted = minted
        self.kite.set_access_token(self.access_token)
        log.info("Reusing cached Kite token minted at %s", minted.strftime("%Y-%m-%d %H:%M"))

    def _persist_token(self) -> None:
        self.settings.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.token_path.write_text(json.dumps({
            "api_key": self.settings.api_key,
            "access_token": self.access_token,
            "minted_at": self.token_minted.isoformat(),
        }, indent=2))

    @property
    def is_authenticated(self) -> bool:
        return bool(self.access_token) and self.token_minted is not None \
            and _token_valid_for(self.token_minted, now_ist())

    def login_url(self) -> str:
        return self.kite.login_url()

    def complete_login(self, request_token: str) -> dict[str, Any]:
        """Exchange a request_token from the redirect for an access token."""
        with self._lock:
            data = self.kite.generate_session(request_token, api_secret=self.settings.api_secret)
            self.access_token = data["access_token"]
            self.token_minted = now_ist()
            self.kite.set_access_token(self.access_token)
            self._persist_token()
        log.info("Kite login complete for user %s", data.get("user_id"))
        return data

    def require_auth(self) -> None:
        if not self.is_authenticated:
            raise KiteAuthError(
                "No valid Kite access token. Open the dashboard and click 'Connect Kite', "
                "or run: python -m pcr.auth_cli"
            )

    # ------------------------------------------------------------- helpers --
    def _call(self, limiter: RateLimiter, fn: Callable[..., Any], *args, **kwargs) -> Any:
        """Invoke a Kite endpoint with throttling and bounded retry on transients."""
        self.require_auth()
        last: Exception | None = None
        for attempt in range(4):
            limiter.wait()
            try:
                return fn(*args, **kwargs)
            except TokenException:
                self.access_token = None
                self.token_minted = None
                raise KiteAuthError("Kite rejected the access token; re-login required.")
            except (NetworkException, DataException) as exc:
                last = exc
                backoff = 0.7 * (2 ** attempt)
                log.warning("Kite call failed (%s); retry %d in %.1fs", exc, attempt + 1, backoff)
                _time.sleep(backoff)
        raise RuntimeError(f"Kite call failed after retries: {last}") from last

    # --------------------------------------------------------------- data --
    def instruments(self, exchange: str) -> list[dict[str, Any]]:
        self.require_auth()
        self._quote_limiter.wait()
        return self.kite.instruments(exchange)

    def quote(self, symbols: Sequence[str]) -> dict[str, Any]:
        """Quote any number of symbols, batching around the 500-instrument cap."""
        merged: dict[str, Any] = {}
        symbols = list(symbols)
        for i in range(0, len(symbols), QUOTE_BATCH_SIZE):
            chunk = symbols[i:i + QUOTE_BATCH_SIZE]
            merged.update(self._call(self._quote_limiter, self.kite.quote, chunk))
        return merged

    def historical(self, instrument_token: int, from_dt: datetime, to_dt: datetime,
                   interval: str = "15minute", oi: bool = False) -> list[dict[str, Any]]:
        return self._call(self._hist_limiter, self.kite.historical_data,
                          instrument_token, from_dt, to_dt, interval, False, oi)

    def profile(self) -> dict[str, Any]:
        return self._call(self._quote_limiter, self.kite.profile)


_session: KiteSession | None = None


def get_session() -> KiteSession:
    """Process-wide singleton so the token cache and throttles are shared."""
    global _session
    if _session is None:
        _session = KiteSession()
    return _session
