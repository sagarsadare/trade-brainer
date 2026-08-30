"""Interactive one-time Kite login. Run once each trading morning:

    python -m pcr.auth_cli

Paste back either the whole redirect URL or just the request_token from it.
"""
from __future__ import annotations

import logging
import sys
from urllib.parse import parse_qs, urlparse

from .kite_client import get_session


def extract_request_token(pasted: str) -> str:
    pasted = pasted.strip()
    if "request_token" in pasted:
        qs = parse_qs(urlparse(pasted).query)
        if qs.get("request_token"):
            return qs["request_token"][0]
    return pasted


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    session = get_session()
    if session.is_authenticated:
        print(f"Already authenticated (token minted {session.token_minted:%Y-%m-%d %H:%M} IST).")
        print("Profile:", session.profile().get("user_name"))
        return 0

    print("\n1. Open this URL and log in to Zerodha:\n")
    print("   " + session.login_url())
    print("\n2. After login your browser lands on the redirect URL.")
    print("   Paste the FULL URL (or just the request_token) here.\n")
    token = extract_request_token(input("request_token> "))
    if not token:
        print("Nothing pasted; aborting.")
        return 1
    data = session.complete_login(token)
    print(f"\nLogged in as {data.get('user_name')} ({data.get('user_id')}).")
    print(f"Token cached at {session.settings.token_path} - valid until ~06:00 IST tomorrow.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
