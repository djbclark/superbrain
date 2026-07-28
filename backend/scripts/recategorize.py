#!/usr/bin/env python3
"""
Metadata-only category taxonomy migration for SQLite SuperBrain databases.

Does NOT redownload media or regenerate transcripts/analyses.
Does NOT use the deprecated MongoDB-era category_manager.

Typical flow:
  1. Place config/categories.toml (from categories.toml.example)
  2. python scripts/recategorize.py backup
  3. python scripts/recategorize.py dry-run --out /tmp/recategorize-report.jsonl
  4. Operator reviews aggregates + sample
  5. python scripts/recategorize.py apply --from-report /tmp/recategorize-report.jsonl
  6. python scripts/recategorize.py suggestions --from-report ...
  7. On failure: python scripts/recategorize.py rollback --backup <path>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from core.classifier import aggregate_suggestions, classify_content
from core.database import Database, DB_PATH
from core.reference_db import (
    copy_reference_row_to_primary,
    open_reference_database,
    resolve_analysis_row,
)
from core.taxonomy import TaxonomyError, clear_taxonomy_cache, get_taxonomy


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _open_db(path: Path) -> Database:
    return Database(path)


def cmd_validate(args: argparse.Namespace) -> int:
    """Load taxonomy config only — no model calls, no DB writes."""
    clear_taxonomy_cache()
    try:
        taxonomy = get_taxonomy(Path(args.config) if args.config else None)
    except TaxonomyError as exc:
        print(f"Invalid taxonomy: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "mode": "validate",
                "config": str(taxonomy.config_path) if taxonomy.config_path else None,
                "loaded_from_file": taxonomy.loaded_from_file,
                "use_default_categories": taxonomy.use_default_categories,
                "allow_multiple_categories": taxonomy.allow_multiple_categories,
                "fallback_category": taxonomy.fallback_category,
                "confidence_threshold": taxonomy.confidence_threshold,
                "taxonomy_version": taxonomy.version,
                "categories": [
                    {
                        "name": c.name,
                        "precedence": c.precedence,
                        "source": c.source,
                    }
                    for c in taxonomy.categories
                ],
            },
            indent=2,
        )
    )
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    src = Path(args.database)
    if not src.is_file():
        print(f"Database not found: {src}", file=sys.stderr)
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = Path(args.output) if args.output else src.with_name(f"{src.name}.bak-{stamp}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Prefer SQLite online backup API for consistency under WAL.
    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
            dest_conn.commit()
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    # Validate backup opens and row counts match.
    with sqlite3.connect(str(src)) as a, sqlite3.connect(str(dest)) as b:
        a_count = a.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        b_count = b.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    if a_count != b_count:
        print(
            f"Backup validation failed: source={a_count} backup={b_count}",
            file=sys.stderr,
        )
        return 1
    print(f"Backup written and validated: {dest} ({b_count} analyses)")
    print(f"BACKUP_PATH={dest}")
    return 0


def _excerpt_for_row(row: dict) -> str:
    parts = [
        row.get("audio_transcription") or "",
        row.get("text_analysis") or "",
        row.get("visual_analysis") or "",
    ]
    return "\n".join(p for p in parts if p)[:4000]


def _fetch_missing_metadata(url: str, cookies: str | None, timeout_s: float) -> dict | None:
    """Best-effort yt-dlp metadata fetch capped by timeout_s (no full analysis)."""
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout

    from analyzers.youtube_analyzer import _cookie_args

    def _run():
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "--no-warnings",
            "--socket-timeout",
            str(max(1, int(timeout_s))),
        ]
        cmd.extend(_cookie_args(cookies))
        cmd.append(url)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout_s),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        import json

        data = json.loads(proc.stdout.splitlines()[0])
        return {
            "title": data.get("title") or "",
            "summary": (data.get("description") or "")[:2000],
            "tags": data.get("tags") or [],
            "username": data.get("uploader") or data.get("channel") or "",
            "thumbnail": data.get("thumbnail") or "",
            "post_date": (
                f"{str(data.get('upload_date'))[:4]}-"
                f"{str(data.get('upload_date'))[4:6]}-"
                f"{str(data.get('upload_date'))[6:8]}"
                if data.get("upload_date") and len(str(data.get("upload_date"))) == 8
                else None
            ),
        }

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        try:
            return fut.result(timeout=timeout_s)
        except FuturesTimeout:
            fut.cancel()
            return None
        except Exception:
            return None


def cmd_playlists(args: argparse.Namespace) -> int:
    """
    Recategorize videos from one or more playlists.

    Uses --reference-database (or SUPERBRAIN_REFERENCE_DATABASE_PATH) to reuse
    transcripts/metadata. Items missing from both DBs get a capped metadata
    fetch (default 20s) before classification. Writes categories when
    --i-understand-this-writes-categories is set.
    """
    if not args.i_understand:
        print(
            "Refusing playlist recategorize without "
            "--i-understand-this-writes-categories",
            file=sys.stderr,
        )
        return 2

    clear_taxonomy_cache()
    try:
        taxonomy = get_taxonomy(Path(args.config) if args.config else None)
    except TaxonomyError as exc:
        print(f"Invalid taxonomy: {exc}", file=sys.stderr)
        return 1

    from analyzers.playlist_analyzer import extract_playlist_urls
    from core.link_checker import validate_link

    primary = _open_db(Path(args.database))
    reference = open_reference_database(args.reference_database)
    if reference:
        print(f"Reference DB (read-only): {reference.db_path}")
    else:
        print("No reference DB configured; using primary only")

    out_path = Path(args.out) if args.out else Path(
        f"/tmp/superbrain-playlist-recat-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if args.resume and out_path.is_file():
        with out_path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sc = rec.get("shortcode")
                if sc:
                    done.add(sc)
        print(f"Resume: skipping {len(done)} already-processed shortcodes")

    # Optional cached URL list to avoid re-extracting playlists on resume.
    url_cache = Path(args.url_cache) if args.url_cache else out_path.with_suffix(".urls.txt")
    if args.resume and url_cache.is_file():
        video_urls = [
            line.strip() for line in url_cache.read_text().splitlines() if line.strip()
        ]
        print(f"Loaded {len(video_urls)} cached playlist URLs from {url_cache}")
    else:
        video_urls = []
        for playlist in args.playlist:
            print(f"Extracting playlist: {playlist}")
            urls = extract_playlist_urls(playlist, cookies=args.cookies)
            print(f"  → {len(urls)} videos")
            video_urls.extend(urls)
        video_urls = list(dict.fromkeys(video_urls))
        url_cache.write_text("\n".join(video_urls) + "\n")
        print(f"Cached playlist URLs → {url_cache}")

    print(f"Unique videos across playlists: {len(video_urls)}")

    stats = {
        "total": len(video_urls),
        "skipped_resume": 0,
        "from_primary": 0,
        "from_reference": 0,
        "missing_fetched": 0,
        "missing_failed": 0,
        "classified": 0,
        "written": 0,
        "write_failed": 0,
        "fallback": 0,
    }
    stats_lock = __import__("threading").Lock()

    def process_one(url: str) -> dict:
        validation = validate_link(url)
        if not validation.get("valid"):
            with stats_lock:
                stats["missing_failed"] += 1
            return {
                "url": url,
                "error": "invalid_url",
                "detail": validation.get("error"),
            }

        shortcode = validation["shortcode"]
        if shortcode in done:
            with stats_lock:
                stats["skipped_resume"] += 1
            return {"shortcode": shortcode, "skipped": True}

        # Per-call DB handle is fine; Database uses thread-local connections.
        row, source = resolve_analysis_row(
            shortcode, primary=primary, reference=reference
        )

        if source == "primary":
            with stats_lock:
                stats["from_primary"] += 1
        elif source == "reference":
            with stats_lock:
                stats["from_reference"] += 1
            copy_reference_row_to_primary(row, primary, preserve_category=True)
        else:
            meta = _fetch_missing_metadata(
                url, args.cookies, float(args.missing_ai_timeout)
            )
            if not meta:
                with stats_lock:
                    stats["missing_failed"] += 1
                return {
                    "shortcode": shortcode,
                    "url": url,
                    "error": "missing_and_fetch_timeout_or_failed",
                    "timeout_s": args.missing_ai_timeout,
                }
            with stats_lock:
                stats["missing_fetched"] += 1
            primary.save_analysis(
                shortcode=shortcode,
                url=url,
                username=meta.get("username") or "",
                title=meta.get("title") or "",
                summary=meta.get("summary") or "",
                tags=meta.get("tags") or [],
                music="",
                category=taxonomy.fallback_category,
                content_type="youtube",
                thumbnail=meta.get("thumbnail") or "",
                post_date=meta.get("post_date"),
                category_source="fallback",
                category_rationale="seeded from capped metadata fetch",
                category_taxonomy_version=taxonomy.version,
            )
            row = primary.get_by_shortcode(shortcode)
            source = "fetched"

        result = classify_content(
            title=row.get("title") or "",
            summary=row.get("summary") or "",
            tags=row.get("tags") or [],
            extra_text=_excerpt_for_row(row),
            taxonomy=taxonomy,
            source="migration",
        )
        with stats_lock:
            stats["classified"] += 1
            if result.source == "fallback":
                stats["fallback"] += 1

        ok = primary.update_category_metadata(
            shortcode,
            category=result.category,
            category_source=result.source,
            category_confidence=result.confidence,
            category_rationale=result.rationale or "",
            category_suggestions_json=result.suggestions or [],
            category_taxonomy_version=taxonomy.version,
        )
        with stats_lock:
            if ok:
                stats["written"] += 1
            else:
                stats["write_failed"] += 1

        return {
            "shortcode": shortcode,
            "url": url,
            "row_source": source,
            "old_category": row.get("category"),
            "new_category": result.category,
            "confidence": result.confidence,
            "source": result.source,
            "fallback_reason": result.fallback_reason,
            "suggestions": result.suggestions,
            "written": ok,
        }

    pending_urls = []
    for url in video_urls:
        validation = validate_link(url)
        sc = validation.get("shortcode") if validation.get("valid") else None
        if sc and sc in done:
            stats["skipped_resume"] += 1
            continue
        pending_urls.append(url)

    print(f"Pending after resume filter: {len(pending_urls)}")
    workers = max(1, int(args.workers))
    mode = "a" if (args.resume and out_path.is_file()) else "w"
    processed = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with out_path.open(mode, encoding="utf-8") as fh, ThreadPoolExecutor(
        max_workers=workers
    ) as pool:
        futures = {pool.submit(process_one, url): url for url in pending_urls}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:
                # Keep the batch alive; one bad item used to abort the whole run
                # (e.g. shared SQLite connection InterfaceError under workers>1).
                with stats_lock:
                    stats["missing_failed"] += 1
                rec = {
                    "url": url,
                    "error": "worker_exception",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
                print(f"worker error on {url}: {rec['detail']}", flush=True)
            if rec.get("skipped"):
                continue
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if rec.get("shortcode"):
                done.add(rec["shortcode"])
            processed += 1
            if args.progress and processed % 10 == 0:
                print(f"progress {processed}/{len(pending_urls)} {stats}", flush=True)

    if reference:
        reference.close()

    summary = {
        "mode": "playlists-apply",
        "generated_at": _utcnow(),
        "database": str(Path(args.database)),
        "reference_database": (
            str(getattr(reference, "db_path", None)) if reference else None
        ),
        "taxonomy_version": taxonomy.version,
        "playlists": list(args.playlist),
        "report": str(out_path),
        "stats": stats,
    }
    summary_path = out_path.with_suffix(out_path.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if stats["write_failed"] == 0 else 1


def cmd_dry_run(args: argparse.Namespace) -> int:
    clear_taxonomy_cache()
    try:
        taxonomy = get_taxonomy(Path(args.config) if args.config else None)
    except TaxonomyError as exc:
        print(f"Invalid taxonomy: {exc}", file=sys.stderr)
        return 1

    db = _open_db(Path(args.database))
    rows = db.list_visible_for_recategorize(limit=args.limit, offset=args.offset)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    change_matrix: dict[str, int] = {}
    fallback_count = 0
    suggestion_lists: list[list[str]] = []

    with out_path.open("w", encoding="utf-8") as fh:
        for i, row in enumerate(rows, start=1):
            result = classify_content(
                title=row.get("title") or "",
                summary=row.get("summary") or "",
                tags=row.get("tags") or [],
                extra_text=_excerpt_for_row(row),
                taxonomy=taxonomy,
                source="migration",
            )
            old = row.get("category") or ""
            record = {
                "shortcode": row["shortcode"],
                "old_category": old,
                "new_category": result.category,
                "confidence": result.confidence,
                "rationale": result.rationale,
                "suggestions": result.suggestions,
                "source": result.source,
                "fallback_reason": result.fallback_reason,
                "taxonomy_version": taxonomy.version,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            counts[result.category] = counts.get(result.category, 0) + 1
            key = f"{old} -> {result.category}"
            change_matrix[key] = change_matrix.get(key, 0) + 1
            if result.source == "fallback":
                fallback_count += 1
            suggestion_lists.append(result.suggestions)
            if args.progress and i % 25 == 0:
                print(f"dry-run progress: {i}/{len(rows)}", flush=True)

    summary = {
        "mode": "dry-run",
        "generated_at": _utcnow(),
        "database": str(Path(args.database)),
        "taxonomy_version": taxonomy.version,
        "taxonomy_names": taxonomy.names,
        "rows": len(rows),
        "assigned_counts": counts,
        "change_matrix": change_matrix,
        "fallback_count": fallback_count,
        "report": str(out_path),
        "suggestion_candidates": aggregate_suggestions(suggestion_lists, taxonomy),
    }
    summary_path = out_path.with_suffix(out_path.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Wrote dry-run report: {out_path}")
    print(f"Wrote summary: {summary_path}")
    print("No database writes were performed.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    report_path = Path(args.from_report)
    if not report_path.is_file():
        print(f"Report not found: {report_path}", file=sys.stderr)
        return 1
    if not args.i_understand:
        print(
            "Refusing to apply without --i-understand-this-writes-categories",
            file=sys.stderr,
        )
        return 2

    clear_taxonomy_cache()
    taxonomy = get_taxonomy(Path(args.config) if args.config else None)
    db = _open_db(Path(args.database))

    state_path = Path(args.state) if args.state else report_path.with_suffix(".apply.state")
    done: set[str] = set()
    if state_path.is_file():
        done = {
            line.strip()
            for line in state_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    applied = 0
    skipped = 0
    failed = 0
    with report_path.open(encoding="utf-8") as fh, state_path.open(
        "a", encoding="utf-8"
    ) as state_fh:
        batch: list[dict] = []

        def flush(batch_rows: list[dict]) -> None:
            nonlocal applied, failed
            for rec in batch_rows:
                success = db.update_category_metadata(
                    rec["shortcode"],
                    category=rec["new_category"],
                    category_source=rec.get("source") or "migration",
                    category_confidence=rec.get("confidence"),
                    category_rationale=rec.get("rationale") or "",
                    category_suggestions_json=rec.get("suggestions") or [],
                    category_taxonomy_version=rec.get("taxonomy_version")
                    or taxonomy.version,
                )
                if success:
                    applied += 1
                    state_fh.write(rec["shortcode"] + "\n")
                    done.add(rec["shortcode"])
                else:
                    failed += 1
                    print(f"Failed to update {rec['shortcode']}", file=sys.stderr)
            state_fh.flush()

        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["shortcode"] in done:
                skipped += 1
                continue
            batch.append(rec)
            if len(batch) >= args.batch_size:
                flush(batch)
                batch.clear()
                if args.progress:
                    print(
                        f"apply progress: applied={applied} failed={failed} skipped={skipped}",
                        flush=True,
                    )
        if batch:
            flush(batch)

    print(
        json.dumps(
            {
                "mode": "apply",
                "applied": applied,
                "failed": failed,
                "skipped_already_done": skipped,
                "state": str(state_path),
            },
            indent=2,
        )
    )
    return 0 if failed == 0 else 1


def _apply_batch(db: Database, batch: list[dict], taxonomy_version: str) -> tuple[int, int]:
    # Kept for unit/manual use; apply command uses inline flush for resume markers.
    ok = 0
    bad = 0
    for rec in batch:
        success = db.update_category_metadata(
            rec["shortcode"],
            category=rec["new_category"],
            category_source=rec.get("source") or "migration",
            category_confidence=rec.get("confidence"),
            category_rationale=rec.get("rationale") or "",
            category_suggestions_json=rec.get("suggestions") or [],
            category_taxonomy_version=rec.get("taxonomy_version") or taxonomy_version,
        )
        if success:
            ok += 1
        else:
            bad += 1
    return ok, bad


def cmd_suggestions(args: argparse.Namespace) -> int:
    report_path = Path(args.from_report)
    taxonomy = get_taxonomy(Path(args.config) if args.config else None)
    lists: list[list[str]] = []
    with report_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            lists.append(rec.get("suggestions") or [])
    candidates = aggregate_suggestions(
        lists, taxonomy, min_count=args.min_count or taxonomy.suggestion_min_count
    )
    print(json.dumps({"suggestion_candidates": candidates}, indent=2, ensure_ascii=False))
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    backup = Path(args.backup)
    dest = Path(args.database)
    if not backup.is_file():
        print(f"Backup not found: {backup}", file=sys.stderr)
        return 1
    if not args.i_understand:
        print(
            "Refusing to rollback without --i-understand-this-restores-database",
            file=sys.stderr,
        )
        return 2

    # Validate backup before replacing live DB.
    with sqlite3.connect(str(backup)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(analyses)").fetchall()
        }
    if "category" not in cols:
        print("Backup does not look like a SuperBrain analyses DB", file=sys.stderr)
        return 1

    # Copy live aside first.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety = dest.with_name(f"{dest.name}.pre-rollback-{stamp}")
    if dest.is_file():
        shutil.copy2(dest, safety)
        print(f"Saved pre-rollback copy: {safety}")

    shutil.copy2(backup, dest)
    # Clear WAL sidecars so SQLite opens the restored file cleanly.
    for suffix in ("-wal", "-shm"):
        side = Path(str(dest) + suffix)
        if side.exists():
            side.unlink()
    print(f"Restored {dest} from {backup} ({count} analyses in backup)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--database",
        default=str(DB_PATH),
        help=f"Path to superbrain.db (default: {DB_PATH})",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to categories.toml (default: backend/config/categories.toml)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="Validate taxonomy config only (no AI, no DB writes)")
    v.set_defaults(func=cmd_validate)

    b = sub.add_parser("backup", help="Create and validate a SQLite backup")
    b.add_argument("--output", default=None, help="Backup destination path")
    b.set_defaults(func=cmd_backup)

    d = sub.add_parser("dry-run", help="Classify all visible rows; write report; no DB writes")
    d.add_argument("--out", required=True, help="JSONL report path")
    d.add_argument("--limit", type=int, default=None)
    d.add_argument("--offset", type=int, default=0)
    d.add_argument("--progress", action="store_true")
    d.set_defaults(func=cmd_dry_run)

    a = sub.add_parser("apply", help="Apply categories from a reviewed dry-run report")
    a.add_argument("--from-report", required=True)
    a.add_argument("--batch-size", type=int, default=50)
    a.add_argument("--state", default=None, help="Resume marker file")
    a.add_argument("--progress", action="store_true")
    a.add_argument(
        "--i-understand-this-writes-categories",
        dest="i_understand",
        action="store_true",
    )
    a.set_defaults(func=cmd_apply)

    s = sub.add_parser("suggestions", help="Aggregate out-of-taxonomy suggestions from a report")
    s.add_argument("--from-report", required=True)
    s.add_argument("--min-count", type=int, default=None)
    s.set_defaults(func=cmd_suggestions)

    r = sub.add_parser("rollback", help="Restore database file from a validated backup")
    r.add_argument("--backup", required=True)
    r.add_argument(
        "--i-understand-this-restores-database",
        dest="i_understand",
        action="store_true",
    )
    r.set_defaults(func=cmd_rollback)

    pl = sub.add_parser(
        "playlists",
        help=(
            "Recategorize playlist videos using optional reference DB for "
            "transcripts; capped fetch for missing items; writes categories"
        ),
    )
    pl.add_argument(
        "--playlist",
        action="append",
        required=True,
        help="Playlist URL (repeatable). Use list=WL for Watch Later.",
    )
    pl.add_argument("--cookies", default=None, help="yt-dlp cookies (e.g. chrome)")
    pl.add_argument(
        "--reference-database",
        default=None,
        help="Read-only SuperBrain DB for transcript/metadata reuse",
    )
    pl.add_argument(
        "--missing-ai-timeout",
        type=float,
        default=20.0,
        help="Seconds capped for metadata fetch when not in primary/reference DB",
    )
    pl.add_argument("--out", default=None, help="JSONL report path")
    pl.add_argument("--url-cache", default=None, help="Optional cached playlist URL list")
    pl.add_argument("--workers", type=int, default=2, help="Parallel classify workers")
    pl.add_argument(
        "--resume",
        action="store_true",
        help="Skip shortcodes already present in --out and reuse URL cache when present",
    )
    pl.add_argument("--progress", action="store_true")
    pl.add_argument(
        "--i-understand-this-writes-categories",
        dest="i_understand",
        action="store_true",
    )
    pl.set_defaults(func=cmd_playlists)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
