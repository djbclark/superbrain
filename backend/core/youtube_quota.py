"""
YouTube Data API quota cost table + durable usage instrumentation.

Cost values follow Google's published YouTube Data API v3 quota costs as of
the COST_TABLE_VERSION date. Unknown methods are recorded as unknown rather
than silently charged as zero.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Bump when the published YouTube quota table is re-verified against docs.
COST_TABLE_VERSION = "2026-07-31"
COST_TABLE_SOURCE = (
    "https://developers.google.com/youtube/v3/determine_quota_cost"
)

# resource -> HTTP method -> units (None = unknown / must be flagged)
_COST_TABLE: dict[str, dict[str, Optional[int]]] = {
    "playlists": {
        "list": 1,
        "insert": 50,
        "update": 50,
        "delete": 50,
    },
    "playlistItems": {
        "list": 1,
        "insert": 50,
        "update": 50,
        "delete": 50,
    },
    "subscriptions": {
        "list": 1,
        "insert": 50,
        "delete": 50,
    },
    "channels": {
        "list": 1,
    },
    "videos": {
        "list": 1,
    },
    "search": {
        "list": 100,
    },
}


@dataclass(frozen=True)
class QuotaCost:
    resource: str
    method: str
    units: Optional[int]
    known: bool
    table_version: str = COST_TABLE_VERSION


def lookup_quota_cost(resource: str, method: str) -> QuotaCost:
    resource = (resource or "").strip()
    method = (method or "").strip().lower()
    methods = _COST_TABLE.get(resource) or {}
    if method in methods:
        units = methods[method]
        return QuotaCost(
            resource=resource,
            method=method,
            units=units,
            known=units is not None,
        )
    return QuotaCost(
        resource=resource,
        method=method,
        units=None,
        known=False,
    )


def estimate_units(resource: str, method: str, *, pages: int = 1) -> int:
    """
    Estimated units for a call. Unknown methods return 0 for budgeting but
    are still recorded with known=false on the event row.
    """
    cost = lookup_quota_cost(resource, method)
    if cost.units is None:
        return 0
    if method == "list":
        return max(1, int(pages)) * cost.units
    return cost.units


def _pacific_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/Los_Angeles")
    except Exception:
        return timezone.utc


def pacific_day_key(now=None) -> str:
    pacific = _pacific_tz()
    current = now or datetime.now(pacific)
    if current.tzinfo is None:
        current = current.replace(tzinfo=pacific)
    else:
        current = current.astimezone(pacific)
    return current.strftime("%Y-%m-%d")


def hours_until_pacific_midnight(now=None) -> float:
    pacific = _pacific_tz()
    current = now or datetime.now(pacific)
    if current.tzinfo is None:
        current = current.replace(tzinfo=pacific)
    else:
        current = current.astimezone(pacific)
    nxt = (current + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(0.0, (nxt - current).total_seconds() / 3600.0)


def classify_result(
    *,
    http_status: Optional[int] = None,
    error: Optional[BaseException] = None,
) -> str:
    if error is not None and http_status is None:
        text = str(error).lower()
        if "403" in text or "quota" in text or "ratelimit" in text:
            return "quota_error"
        if "409" in text or "duplicate" in text:
            return "conflict"
        if "404" in text:
            return "not_found"
        if "401" in text:
            return "auth_error"
        return "error"
    if http_status is None:
        return "ok"
    if 200 <= http_status < 300:
        return "ok"
    if http_status in {403, 429}:
        return "quota_error"
    if http_status == 409:
        return "conflict"
    if http_status == 404:
        return "not_found"
    if http_status == 401:
        return "auth_error"
    if http_status >= 400:
        return "error"
    return "ok"


def record_youtube_api_call(
    db,
    *,
    resource: str,
    method: str,
    http_status: Optional[int] = None,
    result_class: Optional[str] = None,
    duration_ms: Optional[int] = None,
    operation: str = "",
    job_id: str = "",
    retry_count: int = 0,
    pages: int = 1,
    priority: str = "historic",
    error: Optional[BaseException] = None,
    update_ledger: bool = True,
    now=None,
) -> dict[str, Any]:
    """
    Persist one API usage event. Does not store tokens, headers, or payloads.

    Failed/invalid requests are still charged using the published cost when
    known (Google counts them toward quota).
    """
    cost = lookup_quota_cost(resource, method)
    units = estimate_units(resource, method, pages=pages)
    result = result_class or classify_result(http_status=http_status, error=error)
    day = pacific_day_key(now)
    error_class = ""
    if error is not None:
        error_class = type(error).__name__
    elif result != "ok":
        error_class = result

    event = {
        "day_key": day,
        "resource": resource,
        "method": method.lower(),
        "http_status": http_status,
        "result_class": result,
        "units": units,
        "units_known": 1 if cost.known else 0,
        "duration_ms": duration_ms,
        "operation": operation or "",
        "job_id": job_id or "",
        "retry_count": int(retry_count or 0),
        "error_class": error_class,
        "cost_table_version": COST_TABLE_VERSION,
        "priority": "new" if priority == "new" else "historic",
    }
    db.insert_youtube_api_usage_event(**event)

    if update_ledger and units > 0:
        db.record_youtube_quota_spend(
            day,
            units=units,
            priority=event["priority"],
        )
        if result == "quota_error":
            db.mark_youtube_quota_exhausted(day)

    return event


def instrumented_request(
    db,
    *,
    do_request,
    resource: str,
    method: str,
    operation: str = "",
    job_id: str = "",
    priority: str = "historic",
    pages: int = 1,
    retry_count: int = 0,
    update_ledger: bool = True,
    ok_statuses: Optional[set[int]] = None,
):
    """
    Run `do_request()` (callable returning a requests.Response), record usage,
    and re-raise on HTTP errors after recording.

    Status codes in `ok_statuses` are treated as successful (e.g. delete 404).
    """
    started = time.monotonic()
    http_status = None
    allowed = ok_statuses or set()
    try:
        response = do_request()
        http_status = getattr(response, "status_code", None)
        duration_ms = int((time.monotonic() - started) * 1000)
        if http_status in allowed:
            record_youtube_api_call(
                db,
                resource=resource,
                method=method,
                http_status=http_status,
                result_class="ok",
                duration_ms=duration_ms,
                operation=operation,
                job_id=job_id,
                retry_count=retry_count,
                pages=pages,
                priority=priority,
                update_ledger=update_ledger,
            )
            return response
        try:
            response.raise_for_status()
        except Exception as exc:
            record_youtube_api_call(
                db,
                resource=resource,
                method=method,
                http_status=http_status,
                duration_ms=duration_ms,
                operation=operation,
                job_id=job_id,
                retry_count=retry_count,
                pages=pages,
                priority=priority,
                error=exc,
                update_ledger=update_ledger,
            )
            raise
        record_youtube_api_call(
            db,
            resource=resource,
            method=method,
            http_status=http_status,
            duration_ms=duration_ms,
            operation=operation,
            job_id=job_id,
            retry_count=retry_count,
            pages=pages,
            priority=priority,
            update_ledger=update_ledger,
        )
        return response
    except Exception as exc:
        if http_status is None:
            duration_ms = int((time.monotonic() - started) * 1000)
            record_youtube_api_call(
                db,
                resource=resource,
                method=method,
                http_status=None,
                duration_ms=duration_ms,
                operation=operation,
                job_id=job_id,
                retry_count=retry_count,
                pages=pages,
                priority=priority,
                error=exc,
                update_ledger=update_ledger,
            )
        raise


def usage_summary(db, *, day_key: Optional[str] = None, days: int = 1) -> dict[str, Any]:
    """Aggregate usage for one Pacific day or a rolling window of days."""
    day = day_key or pacific_day_key()
    if days <= 1:
        rows = db.summarize_youtube_api_usage(day_key=day)
        by_endpoint = db.summarize_youtube_api_usage_by_endpoint(day_key=day)
        by_operation = db.summarize_youtube_api_usage_by_operation(day_key=day)
        ledger = db.get_youtube_quota_ledger(day)
    else:
        rows = db.summarize_youtube_api_usage(days=days)
        by_endpoint = db.summarize_youtube_api_usage_by_endpoint(days=days)
        by_operation = db.summarize_youtube_api_usage_by_operation(days=days)
        ledger = db.get_youtube_quota_ledger(day)

    return {
        "cost_table_version": COST_TABLE_VERSION,
        "cost_table_source": COST_TABLE_SOURCE,
        "day_key": day,
        "days": days,
        "hours_until_reset": round(hours_until_pacific_midnight(), 2),
        "ledger": ledger,
        "totals": rows,
        "by_endpoint": by_endpoint,
        "by_operation": by_operation,
        "unknown_methods": db.list_unknown_youtube_api_methods(day_key=day if days <= 1 else None, days=days),
    }
