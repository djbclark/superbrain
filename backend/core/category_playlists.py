"""
Mirror SuperBrain taxonomy categories onto YouTube playlists.

Opt-in via [youtube_playlists] in categories.toml (enabled=false by default).
Requires YouTube OAuth with playlist manage scope (see youtube_oauth.SCOPE).
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

# Published YouTube Data API v3 unit costs (local ledger estimates).
UNITS_PLAYLIST_LIST = 1
UNITS_PLAYLIST_INSERT = 50
UNITS_PLAYLIST_ITEM_INSERT = 50
UNITS_PLAYLIST_ITEM_DELETE = 50

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
    daily_quota_units: int = 10000
    new_video_reserve_pct: float = 0.90
    near_reset_hours: float = 2.0
    near_reset_historic_pct: float = 0.90
    fresh_window_hours: float = 24.0
    idle_sleep_seconds: float = 180.0

    def includes(self, category_name: str) -> bool:
        if self.categories is None:
            return True
        wanted = {c.lower() for c in self.categories}
        return category_name.lower() in wanted

    def playlist_title(self, category_name: str) -> str:
        return f"{self.title_prefix}{category_name}"

    @property
    def historic_normal_cap(self) -> int:
        """Max historic units during the normal (non near-reset) phase."""
        reserved = int(self.daily_quota_units * self.new_video_reserve_pct)
        return max(0, self.daily_quota_units - reserved)

    @property
    def near_reset_total_cap(self) -> int:
        """Spend down to this total units ceiling in the near-reset phase."""
        return max(0, int(self.daily_quota_units * self.near_reset_historic_pct))


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

    def _float(key: str, default: float) -> float:
        try:
            return float(section.get(key, default))
        except (TypeError, ValueError):
            return default

    def _int(key: str, default: int) -> int:
        try:
            return int(section.get(key, default))
        except (TypeError, ValueError):
            return default

    return PlaylistSyncConfig(
        enabled=bool(section.get("enabled", False)),
        dry_run=bool(section.get("dry_run", True)),
        categories=cat_tuple,
        title_prefix=str(section.get("title_prefix", "SuperBrain — ")),
        privacy_status=privacy,
        config_path=config_path,
        daily_quota_units=max(1, _int("daily_quota_units", 10000)),
        new_video_reserve_pct=min(1.0, max(0.0, _float("new_video_reserve_pct", 0.90))),
        near_reset_hours=max(0.0, _float("near_reset_hours", 2.0)),
        near_reset_historic_pct=min(
            1.0, max(0.0, _float("near_reset_historic_pct", 0.90))
        ),
        fresh_window_hours=max(0.0, _float("fresh_window_hours", 24.0)),
        idle_sleep_seconds=max(30.0, _float("idle_sleep_seconds", 180.0)),
    )


_SECTION_RE = re.compile(
    r"(?ms)^\[youtube_playlists\]\s*\n.*?(?=^\[|\Z)"
)


def enable_playlist_sync_in_config(
    path: Optional[Path] = None,
    *,
    dry_run: bool = False,
) -> PlaylistSyncConfig:
    """
    Write enabled playlist sync into categories.toml (no hand-editing required).

    Preserves title_prefix, privacy_status, and optional category subset when
    already present. Creates the section if missing.
    """
    config_path = Path(path) if path else Path(
        os.environ.get("SUPERBRAIN_CATEGORIES_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    existing = load_playlist_sync_config(config_path)
    title_prefix = existing.title_prefix or "SuperBrain — "
    privacy = existing.privacy_status or "private"
    lines = [
        "[youtube_playlists]",
        "enabled = true",
        f"dry_run = {'true' if dry_run else 'false'}",
        f'title_prefix = "{title_prefix}"',
        f'privacy_status = "{privacy}"',
        f"daily_quota_units = {existing.daily_quota_units}",
        f"new_video_reserve_pct = {existing.new_video_reserve_pct}",
        f"near_reset_hours = {existing.near_reset_hours}",
        f"near_reset_historic_pct = {existing.near_reset_historic_pct}",
        f"fresh_window_hours = {existing.fresh_window_hours}",
        f"idle_sleep_seconds = {existing.idle_sleep_seconds}",
    ]
    if existing.categories:
        cats = ", ".join(f'"{c}"' for c in existing.categories)
        lines.append(f"categories = [{cats}]")
    section = "\n".join(lines) + "\n"

    if config_path.is_file():
        text = config_path.read_text(encoding="utf-8")
        if _SECTION_RE.search(text):
            text = _SECTION_RE.sub(section, text, count=1)
        else:
            text = text.rstrip() + "\n\n" + section
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        text = section

    config_path.write_text(text, encoding="utf-8")
    logger.info(
        "Enabled [youtube_playlists] in %s (dry_run=%s)",
        config_path,
        dry_run,
    )
    return load_playlist_sync_config(config_path)


def activate_after_oauth(db) -> dict[str, Any]:
    """Enable playlist sync in config and ensure category playlists exist."""
    cfg = enable_playlist_sync_in_config(dry_run=False)
    ensure = ensure_category_playlists(db, config=cfg)
    return {"config": {
        "enabled": cfg.enabled,
        "dry_run": cfg.dry_run,
        "title_prefix": cfg.title_prefix,
        "privacy_status": cfg.privacy_status,
        "config_path": str(cfg.config_path) if cfg.config_path else None,
    }, "ensure": ensure}


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


def _pacific_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/Los_Angeles")
    except Exception:
        return timezone.utc


def pacific_now(now=None) -> datetime:
    pacific = _pacific_tz()
    current = now or datetime.now(pacific)
    if current.tzinfo is None:
        return current.replace(tzinfo=pacific)
    return current.astimezone(pacific)


def pacific_day_key(now=None) -> str:
    return pacific_now(now).strftime("%Y-%m-%d")


def hours_until_pacific_midnight(now=None) -> float:
    current = pacific_now(now)
    nxt = (current + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(0.0, (nxt - current).total_seconds() / 3600.0)


def is_near_quota_reset(cfg: PlaylistSyncConfig, now=None) -> bool:
    return hours_until_pacific_midnight(now) <= cfg.near_reset_hours


def can_spend_quota(
    db,
    cfg: PlaylistSyncConfig,
    *,
    priority: str,
    units: int,
    now=None,
) -> bool:
    """Return whether local budget allows spending `units` for this priority."""
    if units <= 0:
        return True
    if cfg.dry_run:
        return True
    day = pacific_day_key(now)
    ledger = db.ensure_youtube_quota_ledger(day) or {}
    if ledger.get("exhausted_at"):
        return False
    used = int(ledger.get("units_used") or 0)
    historic_used = int(ledger.get("historic_units_used") or 0)
    if used + units > cfg.daily_quota_units:
        return False
    priority = "new" if priority == "new" else "historic"
    if priority == "new":
        return True
    if is_near_quota_reset(cfg, now):
        return used + units <= cfg.near_reset_total_cap
    return historic_used + units <= cfg.historic_normal_cap


def record_quota_spend(
    db,
    cfg: PlaylistSyncConfig,
    *,
    priority: str,
    units: int,
    now=None,
) -> None:
    if units <= 0 or cfg.dry_run:
        return
    day = pacific_day_key(now)
    db.record_youtube_quota_spend(
        day,
        units=units,
        priority="new" if priority == "new" else "historic",
    )


def mark_day_exhausted(db, now=None) -> None:
    db.mark_youtube_quota_exhausted(pacific_day_key(now))


def enqueue_pending_sync(
    db,
    *,
    shortcode: str,
    priority: str,
    category: str = "",
    url: str = "",
    last_error: str = "",
) -> None:
    db.upsert_category_youtube_playlist_pending(
        shortcode=shortcode,
        priority=priority,
        category=category or "",
        url=url or "",
        last_error=last_error or "",
    )


def _parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def priority_for_analysis_row(
    cfg: PlaylistSyncConfig, row: dict[str, Any], *, now=None
) -> str:
    """Fresh analyses (within fresh_window_hours) count as new-video priority."""
    analyzed = _parse_iso_dt(row.get("analyzed_at"))
    if analyzed is None:
        return "historic"
    age = pacific_now(now).astimezone(timezone.utc) - analyzed.astimezone(timezone.utc)
    if age.total_seconds() <= cfg.fresh_window_hours * 3600.0:
        return "new"
    return "historic"


class YouTubePlaylistClient:
    """Thin YouTube Data API v3 client for playlist create/item mutate."""

    def __init__(self, access_token: Optional[str] = None):
        self._token = access_token
        self.last_list_pages = 0

    def _headers(self) -> dict[str, str]:
        token = self._token or youtube_oauth.refresh_access_token()
        self._token = token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def list_my_playlists(self) -> list[dict[str, str]]:
        import requests

        out: list[dict[str, str]] = []
        page_token = ""
        pages = 0
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
            pages += 1
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
                self.last_list_pages = pages
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

    def add_video(
        self, playlist_id: str, video_id: str, *, position: int = 0
    ) -> str:
        import requests

        response = requests.post(
            PLAYLIST_ITEMS_URL,
            timeout=30,
            params={"part": "snippet"},
            headers=self._headers(),
            json={
                "snippet": {
                    "playlistId": playlist_id,
                    "position": int(position),
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
    priority: str = "historic",
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
        if not can_spend_quota(db, cfg, priority=priority, units=UNITS_PLAYLIST_LIST):
            return {
                "ok": False,
                "skipped": "quota_budget",
                "actions": actions,
            }
        try:
            for pl in yt.list_my_playlists():
                if pl["title"] and pl["playlist_id"]:
                    existing_by_title[pl["title"]] = pl["playlist_id"]
            pages_raw = getattr(yt, "last_list_pages", 1)
            try:
                pages = max(1, int(pages_raw or 1))
            except (TypeError, ValueError):
                pages = 1
            record_quota_spend(
                db, cfg, priority=priority, units=UNITS_PLAYLIST_LIST * pages
            )
        except Exception as exc:
            if is_youtube_quota_error(exc):
                mark_day_exhausted(db)
                return {"ok": False, "skipped": "quota_exhausted", "error": str(exc), "actions": actions}
            raise

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
            if not can_spend_quota(
                db, cfg, priority=priority, units=UNITS_PLAYLIST_INSERT
            ):
                actions.append(
                    {
                        "category": name,
                        "action": "skipped_quota_budget",
                        "title": title,
                    }
                )
                continue
            try:
                playlist_id = yt.create_playlist(title, cfg.privacy_status)
            except Exception as exc:
                if is_youtube_quota_error(exc):
                    mark_day_exhausted(db)
                    return {
                        "ok": False,
                        "skipped": "quota_exhausted",
                        "error": str(exc),
                        "actions": actions,
                    }
                raise
            record_quota_spend(
                db, cfg, priority=priority, units=UNITS_PLAYLIST_INSERT
            )
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
    ensure_playlists: bool = True,
    priority: str = "historic",
) -> dict[str, Any]:
    """
    Move a YouTube video into the playlist for new_category.
    Idempotent: re-running with the same category is a no-op.

    priority: "new" (live / fresh) or "historic" (backfill). Budget gating
    prefers new inserts; historic is capped during the normal day phase.

    Inserts use playlist position 0 so watchlists read newest → oldest.

    Set ensure_playlists=False during bulk backfill after a single ensure pass
    so we do not re-list YouTube playlists (and burn quota) on every video.
    """
    cfg = config or load_playlist_sync_config()
    priority = "new" if priority == "new" else "historic"
    result: dict[str, Any] = {
        "shortcode": shortcode,
        "ok": True,
        "skipped": None,
        "actions": [],
        "priority": priority,
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
        db.delete_category_youtube_playlist_pending(shortcode)
        return result

    needs_remove = bool(
        existing
        and existing.get("playlist_item_id")
        and (not resolved or existing.get("category_name") != resolved)
    )
    needs_add = bool(resolved)
    units_needed = 0
    if needs_remove:
        units_needed += UNITS_PLAYLIST_ITEM_DELETE
    if needs_add:
        units_needed += UNITS_PLAYLIST_ITEM_INSERT

    if units_needed and not can_spend_quota(
        db, cfg, priority=priority, units=units_needed
    ):
        # Defer new inserts; historic simply skips until the next budget window.
        if priority == "new":
            enqueue_pending_sync(
                db,
                shortcode=shortcode,
                priority=priority,
                category=resolved or new_category or "",
                url=url,
                last_error="quota_budget",
            )
        result["ok"] = False
        result["skipped"] = "quota_budget"
        return result

    yt = None if cfg.dry_run else (client or YouTubePlaylistClient())

    # Remove from previous category playlist when category changes.
    if needs_remove:
        prev_cat = existing.get("category_name")
        action = {
            "op": "remove",
            "category": prev_cat,
            "playlist_id": existing.get("playlist_id"),
            "playlist_item_id": existing.get("playlist_item_id"),
            "dry_run": cfg.dry_run,
        }
        if cfg.dry_run:
            action["op"] = "would_remove"
            result["actions"].append(action)
        else:
            assert yt is not None
            try:
                yt.remove_playlist_item(existing["playlist_item_id"])
            except Exception as exc:
                if is_youtube_quota_error(exc):
                    mark_day_exhausted(db)
                    enqueue_pending_sync(
                        db,
                        shortcode=shortcode,
                        priority=priority,
                        category=resolved or new_category or "",
                        url=url,
                        last_error=str(exc),
                    )
                    result["ok"] = False
                    result["skipped"] = "quota_exhausted"
                    result["error"] = str(exc)
                    return result
                raise
            record_quota_spend(
                db, cfg, priority=priority, units=UNITS_PLAYLIST_ITEM_DELETE
            )
            db.delete_category_youtube_playlist_item(video_id)
            result["actions"].append(action)

    if not resolved:
        if not result["actions"]:
            result["skipped"] = result["skipped"] or "no_target_category"
        return result

    mapped = db.get_category_youtube_playlist(resolved)
    ensure = None
    if (not mapped or not mapped.get("playlist_id")) and ensure_playlists:
        ensure = ensure_category_playlists(
            db, config=cfg, taxonomy=tax, client=yt, priority=priority
        )
        mapped = db.get_category_youtube_playlist(resolved)
        if cfg.dry_run and not mapped:
            for act in ensure.get("actions", []):
                if act.get("category") == resolved:
                    mapped = {
                        "category_name": resolved,
                        "playlist_id": act.get("playlist_id"),
                        "title": act.get("title"),
                    }
                    break
        if ensure and ensure.get("skipped") in {"quota_budget", "quota_exhausted"}:
            enqueue_pending_sync(
                db,
                shortcode=shortcode,
                priority=priority,
                category=resolved,
                url=url,
                last_error=str(ensure.get("skipped")),
            )
            result["ok"] = False
            result["skipped"] = ensure.get("skipped")
            result["ensure"] = ensure
            return result
    if not mapped or not mapped.get("playlist_id"):
        result["ok"] = False
        result["skipped"] = "playlist_missing"
        if ensure is not None:
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
                "position": 0,
                "dry_run": True,
            }
        )
        return result

    assert yt is not None
    try:
        item_id = yt.add_video(playlist_id, video_id, position=0)
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
                    "position": 0,
                }
            )
        elif is_youtube_quota_error(exc):
            mark_day_exhausted(db)
            enqueue_pending_sync(
                db,
                shortcode=shortcode,
                priority=priority,
                category=resolved,
                url=url,
                last_error=str(exc),
            )
            result["ok"] = False
            result["skipped"] = "quota_exhausted"
            result["error"] = str(exc)
            return result
        else:
            raise
    else:
        record_quota_spend(
            db, cfg, priority=priority, units=UNITS_PLAYLIST_ITEM_INSERT
        )
        result["actions"].append(
            {
                "op": "add",
                "category": resolved,
                "playlist_id": playlist_id,
                "playlist_item_id": item_id,
                "video_id": video_id,
                "position": 0,
            }
        )

    db.upsert_category_youtube_playlist_item(
        video_id=video_id,
        shortcode=shortcode,
        category_name=resolved,
        playlist_id=playlist_id,
        playlist_item_id=item_id,
    )
    db.delete_category_youtube_playlist_pending(shortcode)
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
            priority="new",
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
            "quota_budget",
        }:
            logger.info(
                "[category-playlists] skipped %s: %s",
                shortcode,
                result.get("skipped"),
            )
        elif result.get("skipped") == "quota_budget":
            logger.info(
                "[category-playlists] queued pending %s (quota_budget)",
                shortcode,
            )
    except Exception as exc:
        logger.warning("[category-playlists] sync failed for %s: %s", shortcode, exc)


def ensure_runtime_env_for_cli(*, entrypoint: Path) -> None:
    """Point CLI at the live runtime DB/config and load SecretSpec if needed."""
    import sys

    runtime = Path(
        os.getenv(
            "SUPERBRAIN_RUNTIME_DIR",
            str(Path.home() / ".superbrain-server"),
        )
    )
    os.environ.setdefault("SUPERBRAIN_RUNTIME_DIR", str(runtime))
    os.environ.setdefault("DATABASE_PATH", str(runtime / "superbrain.db"))
    os.environ.setdefault(
        "SUPERBRAIN_CATEGORIES_CONFIG",
        str(runtime / "config" / "categories.toml"),
    )
    if os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN") or os.getenv(
        "SUPERBRAIN_SECRETSPEC_ACTIVE"
    ):
        return
    runner = runtime / "scripts" / "run-with-secrets.sh"
    if not runner.is_file():
        return
    os.environ["SUPERBRAIN_SECRETSPEC_ACTIVE"] = "1"
    os.execv(
        str(runner),
        [str(runner), sys.executable, str(entrypoint.resolve()), *sys.argv[1:]],
    )


def is_youtube_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    if "403" in text or "429" in text:
        return True
    markers = (
        "quota",
        "ratelimit",
        "rate limit",
        "rate_limit",
        "dailylimit",
        "daily limit",
        "usageratelimit",
        "forbidden",
    )
    return any(m in text for m in markers)


def seconds_until_youtube_quota_reset(*, now=None) -> float:
    """YouTube Data API daily quotas reset at midnight Pacific Time."""
    current = pacific_now(now)
    nxt = (current + timedelta(days=1)).replace(
        hour=0, minute=2, second=0, microsecond=0
    )
    return max(60.0, (nxt - current).total_seconds())


def sleep_until_youtube_quota_reset() -> None:
    secs = seconds_until_youtube_quota_reset()
    hours = secs / 3600.0
    print(
        f"YouTube API quota exhausted; sleeping {hours:.1f}h until Pacific midnight reset…",
        flush=True,
    )
    deadline = time.monotonic() + secs
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(300.0, remaining))
        left_h = max(0.0, (deadline - time.monotonic()) / 3600.0)
        if left_h > 0:
            print(f"  …still waiting for quota reset ({left_h:.1f}h left)", flush=True)


def _idle_sleep(cfg: PlaylistSyncConfig, *, reason: str) -> None:
    secs = cfg.idle_sleep_seconds
    print(
        f"Idle {secs:.0f}s ({reason}); historic phase={('near-reset' if is_near_quota_reset(cfg) else 'normal')}",
        flush=True,
    )
    time.sleep(secs)


def _fetch_unsynced_rows(db, *, limit: int = 0) -> list[dict[str, Any]]:
    rows = db._conn.execute(
        """
        SELECT a.shortcode, a.url, a.category, a.content_type,
               COALESCE(a.is_hidden, 0) AS is_hidden,
               a.analyzed_at, a.categorized_at, a.updated_at
        FROM analyses a
        WHERE a.content_type = 'youtube'
          AND COALESCE(a.is_hidden, 0) = 0
          AND a.category IS NOT NULL
          AND a.category != ''
          AND NOT EXISTS (
              SELECT 1 FROM category_youtube_playlist_items i
              WHERE i.shortcode = a.shortcode
                AND i.category_name = a.category
          )
        ORDER BY a.analyzed_at DESC, a.categorized_at DESC, a.updated_at DESC
        """
    ).fetchall()
    out = [dict(r) for r in rows]
    if limit and limit > 0:
        return out[:limit]
    return out


def _sync_one_row(
    db,
    cfg: PlaylistSyncConfig,
    rec: dict[str, Any],
    *,
    priority: str,
) -> dict[str, Any]:
    return sync_video_category(
        db,
        shortcode=rec["shortcode"],
        url=rec.get("url") or "",
        new_category=rec.get("category"),
        is_hidden=bool(rec.get("is_hidden")),
        content_type=rec.get("content_type") or "youtube",
        config=cfg,
        ensure_playlists=False,
        priority=priority,
    )


def print_category_playlists_status() -> int:
    import json
    import sys

    from core.cli_locks import CliLockUnavailable, exclusive_cli_lock
    from core.database import Database
    from core.taxonomy import get_taxonomy

    try:
        with exclusive_cli_lock("category-playlists-status"):
            cfg = load_playlist_sync_config()
            db = Database()
            tax = get_taxonomy(cfg.config_path) if cfg.config_path else get_taxonomy()
            day = pacific_day_key()
            ledger = db.ensure_youtube_quota_ledger(day)
            pending = db.list_category_youtube_playlist_pending()
            print(
                json.dumps(
                    {
                        "config": {
                            "enabled": cfg.enabled,
                            "dry_run": cfg.dry_run,
                            "title_prefix": cfg.title_prefix,
                            "privacy_status": cfg.privacy_status,
                            "daily_quota_units": cfg.daily_quota_units,
                            "new_video_reserve_pct": cfg.new_video_reserve_pct,
                            "near_reset_hours": cfg.near_reset_hours,
                            "near_reset_historic_pct": cfg.near_reset_historic_pct,
                            "categories": list(cfg.categories)
                            if cfg.categories
                            else tax.names,
                        },
                        "quota": {
                            "day_key": day,
                            "near_reset": is_near_quota_reset(cfg),
                            "hours_until_reset": round(
                                hours_until_pacific_midnight(), 2
                            ),
                            "ledger": ledger,
                            "historic_normal_cap": cfg.historic_normal_cap,
                            "near_reset_total_cap": cfg.near_reset_total_cap,
                        },
                        "pending": {
                            "count": len(pending),
                            "new": sum(1 for p in pending if p.get("priority") == "new"),
                            "historic": sum(
                                1 for p in pending if p.get("priority") != "new"
                            ),
                        },
                        "mappings": db.list_category_youtube_playlists(),
                        "synced_items": db._conn.execute(
                            "select count(*) from category_youtube_playlist_items"
                        ).fetchone()[0],
                        "unsynced_estimate": len(_fetch_unsynced_rows(db)),
                        "oauth_refresh_token_set": bool(
                            os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN")
                        ),
                    },
                    indent=2,
                )
            )
            return 0
    except CliLockUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3


def backfill_category_playlists(
    *,
    limit: int = 0,
    continue_on_error: bool = True,
    wait_for_quota: bool = True,
) -> int:
    """
    Quota-aware newest-first playlist backfill (CLI entrypoint).

    Scheduler:
      1. Ensure playlists once (budget-aware).
      2. Drain pending `new` whenever budget allows.
      3. Sync unsynced rows newest-first (fresh window → new, else historic).
      4. Normal day: stop historic at ~10% of daily budget; idle in short sleeps.
      5. Near-reset (last N hours before PT midnight): spend remaining down to
         near_reset_historic_pct of daily, leaving a small buffer for new inserts.
    """
    import json
    import sys

    from core.cli_locks import CliLockUnavailable, exclusive_cli_lock
    from core.database import Database

    try:
        with exclusive_cli_lock("sync-category-playlists"):
            cfg = load_playlist_sync_config()
            if not cfg.enabled:
                print(
                    "disabled: run superbrain --youtube-connect first "
                    "(or set [youtube_playlists] enabled=true)",
                    file=sys.stderr,
                )
                return 2
            db = Database()

            ensure = None
            while True:
                try:
                    ensure = ensure_category_playlists(
                        db, config=cfg, priority="historic"
                    )
                    if ensure.get("skipped") == "quota_exhausted" and wait_for_quota:
                        sleep_until_youtube_quota_reset()
                        db.clear_youtube_quota_exhausted(pacific_day_key())
                        continue
                    break
                except Exception as exc:
                    if wait_for_quota and is_youtube_quota_error(exc):
                        mark_day_exhausted(db)
                        sleep_until_youtube_quota_reset()
                        db.clear_youtube_quota_exhausted(pacific_day_key())
                        continue
                    raise

            ok = skipped = failed = 0
            processed = 0
            max_items = limit if limit and limit > 0 else 0
            last_day = pacific_day_key()

            while True:
                day = pacific_day_key()
                if day != last_day:
                    print(f"Pacific day rolled to {day}; clearing exhausted flag", flush=True)
                    db.clear_youtube_quota_exhausted(day)
                    last_day = day

                ledger = db.ensure_youtube_quota_ledger(day) or {}
                if ledger.get("exhausted_at"):
                    if wait_for_quota:
                        sleep_until_youtube_quota_reset()
                        db.clear_youtube_quota_exhausted(pacific_day_key())
                        continue
                    break

                # 1) Drain pending new first.
                pending_new = db.list_category_youtube_playlist_pending(priority="new")
                made_progress = False
                for pend in pending_new:
                    if max_items and processed >= max_items:
                        break
                    if not can_spend_quota(
                        db, cfg, priority="new", units=UNITS_PLAYLIST_ITEM_INSERT
                    ):
                        break
                    rec = {
                        "shortcode": pend["shortcode"],
                        "url": pend.get("url") or "",
                        "category": pend.get("category") or "",
                        "content_type": "youtube",
                        "is_hidden": 0,
                    }
                    if not rec["category"]:
                        row = db._conn.execute(
                            "SELECT category, url FROM analyses WHERE shortcode = ?",
                            (pend["shortcode"],),
                        ).fetchone()
                        if row:
                            rec["category"] = row["category"] or ""
                            rec["url"] = rec["url"] or (row["url"] or "")
                    try:
                        out = _sync_one_row(db, cfg, rec, priority="new")
                    except Exception as exc:
                        failed += 1
                        processed += 1
                        db.bump_category_youtube_playlist_pending(
                            pend["shortcode"], last_error=str(exc)
                        )
                        print(
                            f"[pending-new] ERROR {pend['shortcode']}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        if not continue_on_error:
                            break
                        continue
                    processed += 1
                    if out.get("skipped") in {"quota_budget", "quota_exhausted"}:
                        break
                    made_progress = True
                    if out.get("skipped"):
                        skipped += 1
                        if out["skipped"] == "already_synced":
                            db.delete_category_youtube_playlist_pending(
                                pend["shortcode"]
                            )
                    elif out.get("ok"):
                        ok += 1
                    else:
                        failed += 1

                if max_items and processed >= max_items:
                    break

                # 2) Drain leftover pending historic when budget allows.
                pending_hist = db.list_category_youtube_playlist_pending(
                    priority="historic"
                )
                for pend in pending_hist:
                    if max_items and processed >= max_items:
                        break
                    if not can_spend_quota(
                        db,
                        cfg,
                        priority="historic",
                        units=UNITS_PLAYLIST_ITEM_INSERT,
                    ):
                        break
                    rec = {
                        "shortcode": pend["shortcode"],
                        "url": pend.get("url") or "",
                        "category": pend.get("category") or "",
                        "content_type": "youtube",
                        "is_hidden": 0,
                    }
                    if not rec["category"]:
                        row = db._conn.execute(
                            "SELECT category, url FROM analyses WHERE shortcode = ?",
                            (pend["shortcode"],),
                        ).fetchone()
                        if row:
                            rec["category"] = row["category"] or ""
                            rec["url"] = rec["url"] or (row["url"] or "")
                    try:
                        out = _sync_one_row(db, cfg, rec, priority="historic")
                    except Exception as exc:
                        failed += 1
                        processed += 1
                        db.bump_category_youtube_playlist_pending(
                            pend["shortcode"], last_error=str(exc)
                        )
                        if not continue_on_error:
                            break
                        continue
                    processed += 1
                    if out.get("skipped") in {"quota_budget", "quota_exhausted"}:
                        break
                    made_progress = True
                    if out.get("skipped"):
                        skipped += 1
                        if out["skipped"] == "already_synced":
                            db.delete_category_youtube_playlist_pending(
                                pend["shortcode"]
                            )
                    elif out.get("ok"):
                        ok += 1
                    else:
                        failed += 1

                if max_items and processed >= max_items:
                    break

                # 3) Newest-first unsynced rows.
                rows = _fetch_unsynced_rows(db)
                if not rows and not db.list_category_youtube_playlist_pending():
                    print("Backfill complete: no unsynced or pending items", flush=True)
                    break

                synced_this_pass = 0
                for rec in rows:
                    if max_items and processed >= max_items:
                        break
                    pri = priority_for_analysis_row(cfg, rec)
                    if pri == "historic" and not can_spend_quota(
                        db,
                        cfg,
                        priority="historic",
                        units=UNITS_PLAYLIST_ITEM_INSERT,
                    ):
                        # Still try new-priority fresh rows later in the list.
                        if not is_near_quota_reset(cfg) and pri == "historic":
                            # Skip this historic item; keep scanning for fresh/new.
                            continue
                        break
                    if pri == "new" and not can_spend_quota(
                        db, cfg, priority="new", units=UNITS_PLAYLIST_ITEM_INSERT
                    ):
                        break
                    try:
                        out = _sync_one_row(db, cfg, rec, priority=pri)
                    except Exception as exc:
                        if is_youtube_quota_error(exc):
                            mark_day_exhausted(db)
                            enqueue_pending_sync(
                                db,
                                shortcode=rec["shortcode"],
                                priority=pri,
                                category=rec.get("category") or "",
                                url=rec.get("url") or "",
                                last_error=str(exc),
                            )
                            break
                        failed += 1
                        processed += 1
                        print(
                            f"ERROR {rec['shortcode']}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        if not continue_on_error:
                            break
                        continue
                    processed += 1
                    if out.get("skipped") == "quota_budget":
                        if pri == "historic":
                            continue
                        break
                    if out.get("skipped") == "quota_exhausted":
                        break
                    made_progress = True
                    synced_this_pass += 1
                    if out.get("skipped"):
                        skipped += 1
                    elif out.get("ok"):
                        ok += 1
                    else:
                        failed += 1
                    if synced_this_pass == 1 or synced_this_pass % 25 == 0:
                        ledger = db.ensure_youtube_quota_ledger(pacific_day_key()) or {}
                        print(
                            f"[sync] ok={ok} skipped={skipped} failed={failed} "
                            f"last={rec['shortcode']} pri={pri} "
                            f"units={ledger.get('units_used')} "
                            f"hist={ledger.get('historic_units_used')} "
                            f"phase={'near-reset' if is_near_quota_reset(cfg) else 'normal'}",
                            flush=True,
                        )

                if max_items and processed >= max_items:
                    break

                # If nothing progressed, idle until phase/budget changes.
                if not made_progress:
                    if not wait_for_quota:
                        break
                    remaining_h = hours_until_pacific_midnight()
                    if remaining_h <= cfg.near_reset_hours:
                        reason = "near-reset waiting for budget/API"
                    else:
                        reason = (
                            f"historic capped at {cfg.historic_normal_cap} units; "
                            f"~{remaining_h:.1f}h until PT midnight"
                        )
                    _idle_sleep(cfg, reason=reason)
                    continue

            ledger = db.ensure_youtube_quota_ledger(pacific_day_key())
            print(
                json.dumps(
                    {
                        "ensure": ensure,
                        "summary": {
                            "ok": ok,
                            "skipped": skipped,
                            "failed": failed,
                            "processed": processed,
                        },
                        "quota": ledger,
                        "pending": len(db.list_category_youtube_playlist_pending()),
                    },
                    indent=2,
                )
            )
            return 0 if failed == 0 else 1
    except CliLockUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3

