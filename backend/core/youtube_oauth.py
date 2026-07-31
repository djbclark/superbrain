"""Minimal OAuth helpers for YouTube subscription discovery and playlist sync."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SUBSCRIPTIONS_URL = "https://www.googleapis.com/youtube/v3/subscriptions"
# Full YouTube scope covers subscriptions (readonly) and playlist create/modify.
# Changing SCOPE requires re-authorization so Google issues a new refresh token.
SCOPE = "https://www.googleapis.com/auth/youtube"

DEFAULT_API_BASE = "http://127.0.0.1:5000"


def _runtime_dir() -> Path:
    return Path(os.getenv("SUPERBRAIN_RUNTIME_DIR", str(Path.home() / ".superbrain-server")))


def last_connect_stamp_path() -> Path:
    return _runtime_dir() / "youtube_oauth.last_connect"


def read_last_connect_at() -> str | None:
    path = last_connect_stamp_path()
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_last_connect_at() -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = last_connect_stamp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamp + "\n", encoding="utf-8")
    return stamp


def configured() -> bool:
    return bool(os.getenv("YOUTUBE_OAUTH_CLIENT_ID") and os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET"))


def new_pkce() -> tuple[str, str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return state, verifier, challenge


def authorization_url(redirect_uri: str, state: str, challenge: str) -> str:
    if not configured():
        raise RuntimeError("YouTube OAuth client is not configured")
    return AUTH_URL + "?" + urlencode({
        "client_id": os.environ["YOUTUBE_OAUTH_CLIENT_ID"], "redirect_uri": redirect_uri,
        "response_type": "code", "scope": SCOPE, "access_type": "offline",
        "prompt": "consent", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def exchange_code(code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
    response = requests.post(TOKEN_URL, timeout=20, data={
        "code": code, "client_id": os.environ["YOUTUBE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_OAUTH_CLIENT_SECRET"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
    })
    response.raise_for_status()
    return response.json()


def refresh_access_token() -> str:
    refresh_token = os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN", "")
    if not refresh_token:
        raise RuntimeError("YouTube has not been authorized yet")
    response = requests.post(TOKEN_URL, timeout=20, data={
        "client_id": os.environ["YOUTUBE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_OAUTH_CLIENT_SECRET"],
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    })
    response.raise_for_status()
    return response.json()["access_token"]


def persist_refresh_token(token: str) -> None:
    manifest = os.getenv("SECRETSPEC_FILE", "secretspec.toml")
    result = subprocess.run(
        ["secretspec", "-f", manifest, "--reason", "Store SuperBrain YouTube OAuth refresh token", "set", "YOUTUBE_OAUTH_REFRESH_TOKEN"],
        input=token + "\n", text=True, capture_output=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Could not store YouTube OAuth refresh token in SecretSpec")
    os.environ["YOUTUBE_OAUTH_REFRESH_TOKEN"] = token
    write_last_connect_at()


def list_subscriptions() -> list[dict[str, str]]:
    token = refresh_access_token()
    items: list[dict[str, str]] = []
    page_token = ""
    db = None
    try:
        from core.database import get_db

        db = get_db()
    except Exception:
        db = None
    while True:
        params = {"part": "snippet", "mine": "true", "maxResults": "50"}
        if page_token:
            params["pageToken"] = page_token

        def _get(params=params, token=token):
            return requests.get(
                SUBSCRIPTIONS_URL,
                timeout=20,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

        if db is not None:
            from core.youtube_quota import instrumented_request

            response = instrumented_request(
                db,
                do_request=_get,
                resource="subscriptions",
                method="list",
                operation="subscription_list",
                priority="new",
                pages=1,
            )
        else:
            response = _get()
            response.raise_for_status()
        payload = response.json()
        for item in payload.get("items", []):
            snippet = item.get("snippet", {})
            cid = snippet.get("resourceId", {}).get("channelId")
            if cid:
                items.append({"channel_id": cid, "title": snippet.get("title", "")})
        page_token = payload.get("nextPageToken", "")
        if not page_token:
            return items


def _http_get_json(url: str) -> tuple[int, Any]:
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


def run_local_browser_connect(
    *,
    base_url: str = DEFAULT_API_BASE,
    token_file: Path | None = None,
    open_browser: bool = True,
    timeout: int = 300,
) -> int:
    """
    Open Google consent via the local API and wait for a fresh callback.

    Used by `superbrain --youtube-connect`. Returns a process exit code.
    """
    from core.cli_locks import CliLockUnavailable, exclusive_cli_lock

    try:
        with exclusive_cli_lock("youtube-connect"):
            return _run_local_browser_connect_unlocked(
                base_url=base_url,
                token_file=token_file,
                open_browser=open_browser,
                timeout=timeout,
            )
    except CliLockUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3


def _run_local_browser_connect_unlocked(
    *,
    base_url: str = DEFAULT_API_BASE,
    token_file: Path | None = None,
    open_browser: bool = True,
    timeout: int = 300,
) -> int:
    token_path = Path(token_file) if token_file else _runtime_dir() / "token.txt"
    if not token_path.is_file():
        print(f"API token file not found: {token_path}", file=sys.stderr)
        return 1
    api_token = token_path.read_text(encoding="utf-8").strip()
    base = base_url.rstrip("/")
    q = urllib.parse.quote(api_token)
    status_url = f"{base}/api/youtube/oauth/status?token={q}"
    code, before = _http_get_json(status_url)
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

    before_stamp = before.get("last_connect_at") or read_last_connect_at()
    connect_url = f"{base}/api/youtube/oauth/start?token={q}"
    print("Opening Google consent in your browser…")
    if open_browser:
        webbrowser.open(connect_url)
    else:
        print(connect_url)

    print("Complete consent in the browser; waiting for localhost callback…")
    deadline = time.monotonic() + max(30, timeout)
    while time.monotonic() < deadline:
        code, payload = _http_get_json(status_url)
        if code == 200 and isinstance(payload, dict) and payload.get("authorized"):
            after_stamp = payload.get("last_connect_at") or read_last_connect_at()
            # First-time auth: any authorized is enough.
            # Re-auth: require a newer connect stamp so an old refresh token
            # does not make us exit before the user finishes consent.
            if not before.get("authorized") or (
                after_stamp and after_stamp != before_stamp
            ):
                print("YouTube connected.")
                print(f"  scope: {payload.get('oauth_scope')}")
                # Callback enables [youtube_playlists] automatically; confirm.
                playlists = payload.get("category_playlists") or {}
                if playlists.get("enabled") and not playlists.get("dry_run"):
                    print("  category playlist sync: enabled")
                elif playlists.get("enabled"):
                    print("  category playlist sync: enabled (dry_run)")
                else:
                    print(
                        "  category playlist sync: not enabled yet "
                        "(callback may still be finishing — check status)"
                    )
                return 0
        time.sleep(2)

    print("Timed out waiting for authorization. Try again.", file=sys.stderr)
    return 1
