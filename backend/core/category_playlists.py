"""
Mirror SuperBrain taxonomy categories onto YouTube playlists.

Opt-in via [youtube_playlists] in categories.toml (enabled=false by default).
Requires YouTube OAuth with playlist manage scope (see youtube_oauth.SCOPE).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore

from core.taxonomy import DEFAULT_CONFIG_PATH, get_taxonomy
from core import youtube_oauth

logger = logging.getLogger(__name__)

PLAYLISTS_URL = "https://www.googleapis.com/youtube/v3/playlists"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

_YT_SHORTCODE_RE = re.compile(r"^YT_([A-Za-z0-9_-]{11})$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class PlaylistSyncConfig:
    enabled: bool = False
    dry_run: bool = True
    categories: Optional[tuple[str, ...]] = None  # None = all taxonomy names
    title_prefix: str = "SuperBrain — "
    privacy_status: str = "private"
    config_path: Optional[Path] = None

    def includes(self, category_name: str) -> bool:
        if self.categories is None:
            return True
        wanted = {c.lower() for c in self.categories}
        return category_name.lower() in wanted

    def playlist_title(self, category_name: str) -> str:
        return f"{self.title_prefix}{category_name}"


def _load_taxonomy(cfg: PlaylistSyncConfig, taxonomy=None):
    if taxonomy is not None:
        return taxonomy
    from core.taxonomy import load_taxonomy, get_taxonomy

    if cfg.config_path and Path(cfg.config_path).is_file():
        return load_taxonomy(Path(cfg.config_path))
    return get_taxonomy()


def load_playlist_sync_config(path: Optional[Path] = None) -> PlaylistSyncConfig:
    """Load [youtube_playlists] from the same categories.toml as taxonomy."""
    config_path = Path(path) if path else Path(
        os.environ.get("SUPERBRAIN_CATEGORIES_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    if not config_path.is_file():
        return PlaylistSyncConfig(config_path=config_path if path else None)
    try:
        data = tomllib.loads(config_path.read_bytes().decode("utf-8"))
    except Exception as exc:
        logger.warning("Could not parse playlist sync config from %s: %s", config_path, exc)
        return PlaylistSyncConfig(config_path=config_path)
    section = data.get("youtube_playlists") or {}
    if not isinstance(section, dict):
        return PlaylistSyncConfig(config_path=config_path)
    cats = section.get("categories")
    cat_tuple: Optional[tuple[str, ...]] = None
    if isinstance(cats, list) and cats:
        cat_tuple = tuple(str(c).strip() for c in cats if str(c).strip())
    privacy = str(section.get("privacy_status", "private")).strip().lower() or "private"
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"
    return PlaylistSyncConfig(
        enabled=bool(section.get("enabled", False)),
        dry_run=bool(section.get("dry_run", True)),
        categories=cat_tuple,
        title_prefix=str(section.get("title_prefix", "SuperBrain — ")),
        privacy_status=privacy,
        config_path=config_path,
    )


def extract_youtube_video_id(shortcode: str = "", url: str = "") -> Optional[str]:
    """Derive an 11-char YouTube video id from YT_* shortcode or watch URL."""
    if shortcode:
        m = _YT_SHORTCODE_RE.match(shortcode.strip())
        if m:
            return m.group(1)
        if _VIDEO_ID_RE.match(shortcode.strip()):
            return shortcode.strip()
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be"}:
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if _VIDEO_ID_RE.match(candidate) else None
    if "youtube" in host or "youtube-nocookie" in host:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            candidate = qs["v"][0]
            return candidate if _VIDEO_ID_RE.match(candidate) else None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
            candidate = parts[1]
            return candidate if _VIDEO_ID_RE.match(candidate) else None
    return None


class YouTubePlaylistClient:
    """Thin YouTube Data API v3 client for playlist create/item mutate."""

    def __init__(self, access_token: Optional[str] = None):
        self._token = access_token

    def _headers(self) -> dict[str, str]:
        token = self._token or youtube_oauth.refresh_access_token()
        self._token = token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def list_my_playlists(self) -> list[dict[str, str]]:
        import requests

        out: list[dict[str, str]] = []
        page_token = ""
        while True:
            params: dict[str, str] = {
                "part": "snippet",
                "mine": "true",
                "maxResults": "50",
            }
            if page_token:
                params["pageToken"] = page_token
            response = requests.get(
                PLAYLISTS_URL, timeout=30, params=params, headers=self._headers()
            )
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("items", []):
                out.append(
                    {
                        "playlist_id": item.get("id", ""),
                        "title": (item.get("snippet") or {}).get("title", ""),
                    }
                )
            page_token = payload.get("nextPageToken", "")
            if not page_token:
                return out

    def create_playlist(self, title: str, privacy_status: str = "private") -> str:
        import requests

        response = requests.post(
            PLAYLISTS_URL,
            timeout=30,
            params={"part": "snippet,status"},
            headers=self._headers(),
            json={
                "snippet": {"title": title},
                "status": {"privacyStatus": privacy_status},
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    def add_video(self, playlist_id: str, video_id: str) -> str:
        import requests

        response = requests.post(
            PLAYLIST_ITEMS_URL,
            timeout=30,
            params={"part": "snippet"},
            headers=self._headers(),
            json={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                }
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    def remove_playlist_item(self, playlist_item_id: str) -> None:
        import requests

        response = requests.delete(
            PLAYLIST_ITEMS_URL,
            timeout=30,
            params={"id": playlist_item_id},
            headers=self._headers(),
        )
        if response.status_code == 404:
            return
        response.raise_for_status()


def ensure_category_playlists(
    db,
    *,
    config: Optional[PlaylistSyncConfig] = None,
    taxonomy=None,
    client: Optional[YouTubePlaylistClient] = None,
) -> dict[str, Any]:
    """Create or adopt YouTube playlists for each synced taxonomy category."""
    cfg = config or load_playlist_sync_config()
    tax = _load_taxonomy(cfg, taxonomy)
    actions: list[dict[str, Any]] = []
    if not cfg.enabled:
        return {"ok": True, "skipped": "disabled", "actions": actions}

    names = [c.name for c in tax.categories if cfg.includes(c.name)]
    # Prefer user categories only when defaults are disabled (this deployment).
    if not tax.use_default_categories:
        names = [c.name for c in tax.categories if c.source == "user" and cfg.includes(c.name)]

    existing_by_title: dict[str, str] = {}
    yt = client
    if not cfg.dry_run:
        yt = yt or YouTubePlaylistClient()
        for pl in yt.list_my_playlists():
            if pl["title"] and pl["playlist_id"]:
                existing_by_title[pl["title"]] = pl["playlist_id"]

    for name in names:
        title = cfg.playlist_title(name)
        mapped = db.get_category_youtube_playlist(name)
        if mapped and mapped.get("playlist_id"):
            actions.append(
                {
                    "category": name,
                    "action": "mapped",
                    "playlist_id": mapped["playlist_id"],
                    "title": mapped.get("title") or title,
                }
            )
            continue
        playlist_id = existing_by_title.get(title)
        if playlist_id:
            action = "adopted"
        elif cfg.dry_run:
            playlist_id = f"dryrun-{name.lower()}"
            action = "would_create"
        else:
            assert yt is not None
            playlist_id = yt.create_playlist(title, cfg.privacy_status)
            action = "created"
        if not cfg.dry_run and not playlist_id.startswith("dryrun-"):
            db.upsert_category_youtube_playlist(name, playlist_id, title)
        actions.append(
            {
                "category": name,
                "action": action,
                "playlist_id": playlist_id,
                "title": title,
                "dry_run": cfg.dry_run,
            }
        )
    return {"ok": True, "dry_run": cfg.dry_run, "actions": actions}


def sync_video_category(
    db,
    *,
    shortcode: str,
    url: str = "",
    new_category: Optional[str],
    old_category: Optional[str] = None,
    is_hidden: bool = False,
    content_type: str = "youtube",
    config: Optional[PlaylistSyncConfig] = None,
    client: Optional[YouTubePlaylistClient] = None,
) -> dict[str, Any]:
    """
    Move a YouTube video into the playlist for new_category.
    Idempotent: re-running with the same category is a no-op.
    """
    cfg = config or load_playlist_sync_config()
    result: dict[str, Any] = {
        "shortcode": shortcode,
        "ok": True,
        "skipped": None,
        "actions": [],
    }
    if not cfg.enabled:
        result["skipped"] = "disabled"
        return result
    if content_type and content_type != "youtube":
        result["skipped"] = "not_youtube"
        return result
    if is_hidden:
        result["skipped"] = "hidden"
        return result

    video_id = extract_youtube_video_id(shortcode, url)
    if not video_id:
        result["ok"] = False
        result["skipped"] = "no_video_id"
        return result

    tax = _load_taxonomy(cfg)
    resolved = tax.resolve_name(new_category or "") if new_category else None
    if new_category and not resolved:
        result["skipped"] = "category_outside_taxonomy"
        return result
    if resolved and not cfg.includes(resolved):
        result["skipped"] = "category_not_in_sync_set"
        # Still remove from previous playlist if we tracked one.
        resolved = None

    existing = db.get_category_youtube_playlist_item(video_id)
    if existing and resolved and existing.get("category_name") == resolved:
        result["skipped"] = "already_synced"
        return result

    yt = None if cfg.dry_run else (client or YouTubePlaylistClient())

    # Remove from previous category playlist when category changes.
    if existing and existing.get("playlist_item_id"):
        prev_cat = existing.get("category_name")
        if not resolved or prev_cat != resolved:
            action = {
                "op": "remove",
                "category": prev_cat,
                "playlist_id": existing.get("playlist_id"),
                "playlist_item_id": existing.get("playlist_item_id"),
                "dry_run": cfg.dry_run,
            }
            if cfg.dry_run:
                action["op"] = "would_remove"
            else:
                assert yt is not None
                yt.remove_playlist_item(existing["playlist_item_id"])
                db.delete_category_youtube_playlist_item(video_id)
            result["actions"].append(action)

    if not resolved:
        if not result["actions"]:
            result["skipped"] = result["skipped"] or "no_target_category"
        return result

    # Ensure destination playlist mapping exists.
    ensure = ensure_category_playlists(db, config=cfg, taxonomy=tax, client=yt)
    mapped = db.get_category_youtube_playlist(resolved)
    if cfg.dry_run and not mapped:
        # Dry-run ensure does not persist; synthesize from ensure actions.
        for act in ensure.get("actions", []):
            if act.get("category") == resolved:
                mapped = {
                    "category_name": resolved,
                    "playlist_id": act.get("playlist_id"),
                    "title": act.get("title"),
                }
                break
    if not mapped or not mapped.get("playlist_id"):
        result["ok"] = False
        result["skipped"] = "playlist_missing"
        result["ensure"] = ensure
        return result

    playlist_id = mapped["playlist_id"]
    if cfg.dry_run:
        result["actions"].append(
            {
                "op": "would_add",
                "category": resolved,
                "playlist_id": playlist_id,
                "video_id": video_id,
                "dry_run": True,
            }
        )
        return result

    assert yt is not None
    try:
        item_id = yt.add_video(playlist_id, video_id)
    except Exception as exc:
        # Duplicate playlist item often returns 409; treat as already present.
        msg = str(exc)
        if "409" in msg or "duplicate" in msg.lower():
            item_id = existing.get("playlist_item_id") if existing else None
            item_id = item_id or f"duplicate:{video_id}"
            result["actions"].append(
                {
                    "op": "already_on_playlist",
                    "category": resolved,
                    "playlist_id": playlist_id,
                    "video_id": video_id,
                }
            )
        else:
            raise
    else:
        result["actions"].append(
            {
                "op": "add",
                "category": resolved,
                "playlist_id": playlist_id,
                "playlist_item_id": item_id,
                "video_id": video_id,
            }
        )

    db.upsert_category_youtube_playlist_item(
        video_id=video_id,
        shortcode=shortcode,
        category_name=resolved,
        playlist_id=playlist_id,
        playlist_item_id=item_id,
    )
    return result


def maybe_sync_after_category_change(
    db,
    *,
    shortcode: str,
    url: str = "",
    new_category: Optional[str],
    old_category: Optional[str] = None,
    is_hidden: bool = False,
    content_type: str = "youtube",
) -> None:
    """Best-effort hook for analyze/edit paths; never raises to callers."""
    try:
        cfg = load_playlist_sync_config()
        if not cfg.enabled:
            return
        result = sync_video_category(
            db,
            shortcode=shortcode,
            url=url,
            new_category=new_category,
            old_category=old_category,
            is_hidden=is_hidden,
            content_type=content_type,
            config=cfg,
        )
        if result.get("actions"):
            logger.info(
                "[category-playlists] %s %s",
                shortcode,
                result.get("actions"),
            )
        elif result.get("skipped") and result["skipped"] not in {
            "disabled",
            "already_synced",
            "not_youtube",
        }:
            logger.info(
                "[category-playlists] skipped %s: %s",
                shortcode,
                result.get("skipped"),
            )
    except Exception as exc:
        logger.warning("[category-playlists] sync failed for %s: %s", shortcode, exc)
