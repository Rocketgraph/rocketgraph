"""
Loki log connector — HTTP query_range with Bearer auth.

credentials keys:
    url      – Loki base URL, e.g. https://loki.example.com  (required)
    api_key  – Bearer token                                  (required)
    query    – LogQL selector, default {service_name=~".+"}
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

PAGE_SIZE = 3000
_TS_MULT  = 1_000_000_000   # s → ns

_SERVICE_LABELS = ("service_name", "service", "app", "container", "job")
_LEVEL_LABELS   = ("level", "severity", "log_level", "loglevel")
_LEVEL_RE       = re.compile(
    r'\b(debug|info|warn(?:ing)?|error|err|crit(?:ical)?|fatal|trace)\b', re.I
)
_LEVEL_NORM     = {
    "warning": "warn", "err": "error",
    "critical": "error", "fatal": "error", "crit": "error",
}


def _service(labels: dict) -> str:
    for k in _SERVICE_LABELS:
        if labels.get(k):
            return labels[k]
    return "unknown"


def _level_from_labels(labels: dict) -> str | None:
    for k in _LEVEL_LABELS:
        v = (labels.get(k) or "").strip().lower()
        if v:
            return _LEVEL_NORM.get(v, v)
    return None


def _level_from_line(line: str) -> str:
    m = _LEVEL_RE.search(line)
    if not m:
        return "info"
    raw = m.group(1).lower()
    return _LEVEL_NORM.get(raw, raw)


def _query_range(url: str, api_key: str, query: str, start_ns: int, end_ns: int) -> list[dict]:
    r = requests.get(
        f"{url.rstrip('/')}/loki/api/v1/query_range",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        params={
            "query":     query,
            "start":     str(start_ns),
            "end":       str(end_ns),
            "limit":     str(PAGE_SIZE),
            "direction": "forward",
        },
        timeout=120,
    )
    if r.status_code in (401, 403):
        raise RuntimeError("Loki auth failed — check url and api_key (Bearer token).")
    r.raise_for_status()

    rows: list[dict] = []
    for stream in r.json().get("data", {}).get("result", []):
        labels  = stream.get("stream", {})
        service = _service(labels)
        lvl_lbl = _level_from_labels(labels)
        for ts_ns_str, line in stream.get("values", []):
            rows.append({
                "timestamp": int(ts_ns_str) // 1_000_000,   # ns → ms
                "message":   line,
                "level":     lvl_lbl or _level_from_line(line),
                "service":   service,
            })
    return rows


def fetch_logs(credentials: dict, lookback_hours: int = 6, max_rows: int = 100_000) -> list[dict]:
    url     = credentials.get("url")
    api_key = credentials.get("api_key")
    query   = credentials.get("query") or '{service_name=~".+"}'
    if not url or not api_key:
        raise RuntimeError("Loki requires credentials.url and credentials.api_key")

    now   = datetime.now(timezone.utc)
    start = now - timedelta(hours=lookback_hours)

    all_rows: list[dict]    = []
    cursor:   datetime      = start
    page:     int           = 0

    while len(all_rows) < max_rows and cursor < now:
        rows = _query_range(
            url, api_key, query,
            int(cursor.timestamp() * _TS_MULT),
            int(now.timestamp()    * _TS_MULT),
        )
        all_rows.extend(rows)
        page += 1
        if len(rows) < PAGE_SIZE:
            break
        last_ms = rows[-1]["timestamp"]
        cursor  = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc) + timedelta(milliseconds=1)

    # Deduplicate
    seen, out = set(), []
    for r in all_rows:
        key = (r["timestamp"], r["message"][:80])
        if key not in seen:
            seen.add(key)
            out.append(r)

    log.info(f"[loki] {len(out)} rows in {page} pages")
    return out[:max_rows]
