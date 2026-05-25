"""
Datadog log connector — Logs Search API v2 with cursor pagination.

credentials keys:
    api_key  – DD_API_KEY            (required)
    app_key  – DD_APPLICATION_KEY    (required, scope logs:read)
    site     – datadoghq.com | datadoghq.eu | us3.datadoghq.com | ddog-gov.com
    query    – DD search query, default "*"
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

PAGE_SIZE = 1000

_LEVEL_NORM = {
    "warning":  "warn",
    "critical": "error",
    "fatal":    "error",
    "ok":       "info",
    "notice":   "info",
    "trace":    "debug",
}


def _normalise_level(raw: str) -> str:
    v = (raw or "").strip().lower()
    return _LEVEL_NORM.get(v, v) if v else "info"


def _fetch_page(
    api_key: str, app_key: str, site: str, query: str,
    from_iso: str, to_iso: str, cursor: Optional[str],
) -> tuple[list[dict], Optional[str]]:
    body = {
        "filter": {"query": query, "from": from_iso, "to": to_iso, "indexes": ["*"]},
        "page":   {"limit": PAGE_SIZE},
        "sort":   "timestamp",
    }
    if cursor:
        body["page"]["cursor"] = cursor

    r = requests.post(
        f"https://api.{site.rstrip('/')}/api/v2/logs/events/search",
        headers={
            "Content-Type":       "application/json",
            "DD-API-KEY":         api_key,
            "DD-APPLICATION-KEY": app_key,
        },
        json=body,
        timeout=30,
    )
    if r.status_code in (401, 403):
        raise RuntimeError("Datadog auth failed — check api_key and app_key (needs logs:read).")
    r.raise_for_status()
    data         = r.json()
    raw_logs     = data.get("data", [])
    next_cursor  = data.get("meta", {}).get("page", {}).get("after")

    rows: list[dict] = []
    for entry in raw_logs:
        attrs = entry.get("attributes", {})
        ts_ms = 0
        try:
            ts_ms = int(datetime.fromisoformat(
                attrs.get("timestamp", "").replace("Z", "+00:00")
            ).timestamp() * 1000)
        except (ValueError, AttributeError):
            pass
        rows.append({
            "timestamp": ts_ms,
            "message":   str(attrs.get("message") or attrs.get("content") or ""),
            "level":     _normalise_level(str(attrs.get("status") or "info")),
            "service":   attrs.get("service") or "unknown",
        })
    return rows, next_cursor if len(raw_logs) == PAGE_SIZE else None


def fetch_logs(credentials: dict, lookback_hours: int = 6, max_rows: int = 100_000) -> list[dict]:
    api_key = credentials.get("api_key")
    app_key = credentials.get("app_key")
    site    = (credentials.get("site") or "datadoghq.com").rstrip("/")
    query   = credentials.get("query") or "*"
    if not api_key or not app_key:
        raise RuntimeError("Datadog requires credentials.api_key and credentials.app_key")

    now      = datetime.now(timezone.utc)
    start    = now - timedelta(hours=lookback_hours)
    from_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_rows: list[dict]    = []
    cursor:   Optional[str] = None
    pages = 0
    while len(all_rows) < max_rows:
        rows, cursor = _fetch_page(api_key, app_key, site, query, from_iso, to_iso, cursor)
        all_rows.extend(rows)
        pages += 1
        if not cursor:
            break

    seen, out = set(), []
    for r in all_rows:
        key = (r["timestamp"], r["message"][:80])
        if key not in seen:
            seen.add(key)
            out.append(r)
    log.info(f"[datadog] {len(out)} rows across {pages} pages")
    return out[:max_rows]
