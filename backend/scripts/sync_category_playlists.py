#!/usr/bin/env python3
"""Ensure / backfill YouTube playlists for SuperBrain taxonomy categories.

Examples:
  python scripts/sync_category_playlists.py status
  python scripts/sync_category_playlists.py ensure
  python scripts/sync_category_playlists.py sync-one YT_dQw4w9WgXcQ
  python scripts/sync_category_playlists.py sync-all --limit 50
  python scripts/sync_category_playlists.py sync-all --force-write   # ignore dry_run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.category_playlists import (
    ensure_category_playlists,
    load_playlist_sync_config,
    sync_video_category,
)
from core.database import Database
from core.taxonomy import get_taxonomy


def _cfg(args) -> object:
    cfg = load_playlist_sync_config(
        Path(args.config) if args.config else None
    )
    if args.force_write:
        cfg = replace(cfg, enabled=True, dry_run=False)
    elif args.enable:
        cfg = replace(cfg, enabled=True)
    return cfg


def cmd_status(args) -> int:
    cfg = _cfg(args)
    db = Database(db_path=args.database) if args.database else Database()
    tax = get_taxonomy(Path(args.config) if args.config else None)
    payload = {
        "config": {
            "enabled": cfg.enabled,
            "dry_run": cfg.dry_run,
            "title_prefix": cfg.title_prefix,
            "privacy_status": cfg.privacy_status,
            "categories": list(cfg.categories) if cfg.categories else tax.names,
        },
        "mappings": db.list_category_youtube_playlists(),
        "oauth_refresh_token_set": bool(os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN")),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_ensure(args) -> int:
    cfg = _cfg(args)
    if not cfg.enabled:
        print("disabled: set [youtube_playlists] enabled=true or pass --enable", file=sys.stderr)
        return 2
    db = Database(db_path=args.database) if args.database else Database()
    result = ensure_category_playlists(db, config=cfg)
    print(json.dumps(result, indent=2))
    return 0


def cmd_sync_one(args) -> int:
    cfg = _cfg(args)
    if not cfg.enabled:
        print("disabled: set [youtube_playlists] enabled=true or pass --enable", file=sys.stderr)
        return 2
    db = Database(db_path=args.database) if args.database else Database()
    row = db.get_by_shortcode(args.shortcode)
    if not row:
        print(f"not found: {args.shortcode}", file=sys.stderr)
        return 1
    result = sync_video_category(
        db,
        shortcode=args.shortcode,
        url=row.get("url") or "",
        new_category=row.get("category"),
        is_hidden=bool(row.get("is_hidden")),
        content_type=row.get("content_type") or "youtube",
        config=cfg,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_sync_all(args) -> int:
    cfg = _cfg(args)
    if not cfg.enabled:
        print("disabled: set [youtube_playlists] enabled=true or pass --enable", file=sys.stderr)
        return 2
    db = Database(db_path=args.database) if args.database else Database()
    ensure = ensure_category_playlists(db, config=cfg)
    rows = db._conn.execute(
        """
        SELECT shortcode, url, category, content_type, COALESCE(is_hidden, 0) AS is_hidden
        FROM analyses
        WHERE content_type = 'youtube'
          AND COALESCE(is_hidden, 0) = 0
          AND category IS NOT NULL
          AND category != ''
        ORDER BY categorized_at DESC, updated_at DESC
        """
    ).fetchall()
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    results = []
    ok = 0
    failed = 0
    skipped = 0
    for index, row in enumerate(rows, 1):
        rec = dict(row)
        try:
            out = sync_video_category(
                db,
                shortcode=rec["shortcode"],
                url=rec.get("url") or "",
                new_category=rec.get("category"),
                is_hidden=bool(rec.get("is_hidden")),
                content_type=rec.get("content_type") or "youtube",
                config=cfg,
            )
            if args.verbose:
                results.append(out)
            if out.get("skipped"):
                skipped += 1
            elif out.get("ok"):
                ok += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            err = {"shortcode": rec["shortcode"], "ok": False, "error": str(exc)}
            if args.verbose:
                results.append(err)
            print(
                f"[{index}/{len(rows)}] ERROR {rec['shortcode']}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            if not args.continue_on_error:
                break
        if index == 1 or index % 25 == 0 or index == len(rows):
            print(
                f"[{index}/{len(rows)}] ok={ok} skipped={skipped} failed={failed} "
                f"last={rec['shortcode']}",
                flush=True,
            )
    print(
        json.dumps(
            {
                "ensure": ensure,
                "summary": {"ok": ok, "skipped": skipped, "failed": failed, "total": len(rows)},
                "results": results if args.verbose else None,
            },
            indent=2,
        )
    )
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", help="SQLite path (default: DATABASE_PATH / runtime db)")
    parser.add_argument("--config", help="categories.toml path")
    parser.add_argument(
        "--enable",
        action="store_true",
        help="Treat sync as enabled for this run (still respects dry_run unless --force-write)",
    )
    parser.add_argument(
        "--force-write",
        action="store_true",
        help="Force enabled=true and dry_run=false for this run (mutates YouTube)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Show config + local playlist mappings")
    p_status.set_defaults(func=cmd_status)

    p_ensure = sub.add_parser("ensure", help="Create/adopt playlists for taxonomy categories")
    p_ensure.set_defaults(func=cmd_ensure)

    p_one = sub.add_parser("sync-one", help="Sync one shortcode")
    p_one.add_argument("shortcode")
    p_one.set_defaults(func=cmd_sync_one)

    p_all = sub.add_parser("sync-all", help="Backfill youtube analyses into category playlists")
    p_all.add_argument("--limit", type=int, default=0, help="Max rows (0 = all)")
    p_all.add_argument("--continue-on-error", action="store_true")
    p_all.add_argument("--verbose", action="store_true")
    p_all.set_defaults(func=cmd_sync_all)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
