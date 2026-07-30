"""Minimal OAuth helpers for private YouTube subscription discovery."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import subprocess
import time
from typing import Any
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SUBSCRIPTIONS_URL = "https://www.googleapis.com/youtube/v3/subscriptions"
SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


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


def list_subscriptions() -> list[dict[str, str]]:
    token = refresh_access_token()
    items: list[dict[str, str]] = []
    page_token = ""
    while True:
        params = {"part": "snippet", "mine": "true", "maxResults": "50"}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(SUBSCRIPTIONS_URL, timeout=20, params=params,
                                headers={"Authorization": f"Bearer {token}"})
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
