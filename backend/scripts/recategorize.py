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
from core.taxonomy import TaxonomyError, clear_taxonomy_cache, get_taxonomy


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _open_db(path: Path) -> Database:
    return Database(path)


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
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
