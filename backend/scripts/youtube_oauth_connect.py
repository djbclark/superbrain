#!/usr/bin/env python3
"""One-step YouTube OAuth connect for the local SuperBrain API.

Opens the Google consent page in your browser, then waits until SuperBrain
stores a refresh token via the localhost callback.

  # Prefer secretspec so YOUTUBE_OAUTH_CLIENT_* are available to the API already;
  # this script only needs the API token + a running server:
  python scripts/youtube_oauth_connect.py

  python scripts/youtube_oauth_connect.py --no-open   # print URL only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

RUNTIME = Path.home() / ".superbrain-server"
DEFAULT_BASE = "http://127.0.0.1:5000"


def _load_token(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"API token file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _get(url: str) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return resp.status, json.loads(body)
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="API base URL")
    parser.add_argument(
        "--token-file",
        default=str(RUNTIME / "token.txt"),
        help="Path to API token.txt",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print the connect URL instead of opening a browser",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for authorization (default 300)",
    )
    args = parser.parse_args()

    token = _load_token(Path(args.token_file))
    status_url = f"{args.base.rstrip('/')}/api/youtube/oauth/status?token={urllib.parse.quote(token)}"
    code, before = _get(status_url)
    if code != 200 or not isinstance(before, dict):
        print(f"Cannot reach API status ({code}): {before}", file=sys.stderr)
        return 1
    if not before.get("configured"):
        print(
            "YouTube OAuth client is not configured "
            "(YOUTUBE_OAUTH_CLIENT_ID / SECRET missing in SecretSpec).",
            file=sys.stderr,
        )
        return 1

    connect_url = (
        f"{args.base.rstrip('/')}/api/youtube/oauth/start"
        f"?token={urllib.parse.quote(token)}"
    )
    print("Opening Google consent in your browser…")
    print(f"  {args.base.rstrip('/')}/api/youtube/oauth/start?token=…")
    if args.no_open:
        print(connect_url)
    else:
        webbrowser.open(connect_url)

    print("Waiting for localhost callback to store the refresh token…")
    deadline = time.monotonic() + max(30, args.timeout)
    while time.monotonic() < deadline:
        code, payload = _get(status_url)
        if code == 200 and isinstance(payload, dict) and payload.get("authorized"):
            print("YouTube connected.")
            print(f"  scope: {payload.get('oauth_scope')}")
            return 0
        time.sleep(2)

    print("Timed out waiting for authorization. Try again.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
