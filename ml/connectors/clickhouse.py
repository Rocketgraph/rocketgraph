"""
ClickHouse connector — HTTP interface (port 8123) with optional basic auth.

credentials keys:
    url            – http://clickhouse.host:8123  (required)
    user           – default
    password       – ""
    database       – default
    table          – logs
    col_timestamp  – timestamp
    col_message    – message
    col_level      – level
    col_service    – service
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

PAGE_SIZE = 100_000

_LEVEL_NORM = {
    "warning":  "warn",
    "critical": "error",
    "fatal":    "error",
    "trace":    "debug",
    "notice":   "info",
    "ok":       "info",
}
_LEVEL_RE = re.compile(
    r'\b(debug|info|warn(?:ing)?|error|err|crit(?:ical)?|fatal|trace)\b', re.I
)


def _normalise_level(raw: str) -> str:
    v = (raw or "").strip().lower()
    return _LEVEL_NORM.get(v, v) if v else "info"


def _level_from_line(line: str) -> str:
    m = _LEVEL_RE.search(line)
    if not m:
        return "info"
    return _LEVEL_NORM.get(m.group(1).lower(), m.group(1).lower())


def _query(sql: str, url: str, user: str, password: str) -> list[dict]:
    auth = (user, password or "") if user else None
    r = requests.post(
        url.rstrip("/"),
        data=f"{sql} FORMAT JSONEachRow".encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        auth=auth,
        timeout=300,
    )
    if r.status_code in (401, 403):
        raise RuntimeError("ClickHouse auth failed — check user/password.")
    if not r.ok:
        raise RuntimeError(f"ClickHouse {r.status_code} — {r.text[:500]}")
    out: list[dict] = []
    for line in r.text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def fetch_logs(
    credentials: dict,
    lookback_hours: int = 6,
    max_rows: int = 100_000,
    start: Optional[datetime] = None,
    end:   Optional[datetime] = None,
) -> list[dict]:
    url      = credentials.get("url")
    if not url:
        raise RuntimeError("ClickHouse requires credentials.url")
    user     = credentials.get("user")     or "default"
    password = credentials.get("password") or ""
    database = credentials.get("database") or "default"
    table    = f"{database}.{credentials.get('table') or 'logs'}"
    col_ts   = credentials.get("col_timestamp") or "timestamp"
    col_msg  = credentials.get("col_message")   or "message"
    col_lvl  = credentials.get("col_level")     or "level"
    col_svc  = credentials.get("col_service")   or "service"

    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(hours=lookback_hours)
    fmt   = "%Y-%m-%d %H:%M:%S"

    rows: list[dict] = []
    offset = 0
    pages = 0
    while len(rows) < max_rows:
        limit = min(PAGE_SIZE, max_rows - len(rows))
        sql = (
            f"SELECT "
            f"  toUnixTimestamp64Milli({col_ts}) AS _ts_ms, "
            f"  toString({col_msg}) AS _message, "
            f"  toString({col_lvl}) AS _level, "
            f"  toString({col_svc}) AS _service "
            f"FROM {table} "
            f"WHERE {col_ts} >= toDateTime64('{start.strftime(fmt)}', 9, 'UTC') "
            f"  AND {col_ts} <  toDateTime64('{end.strftime(fmt)}', 9, 'UTC') "
            f"ORDER BY {col_ts} ASC "
            f"LIMIT {limit} OFFSET {offset}"
        )
        raw = _query(sql, url, user, password)
        pages += 1
        for r in raw:
            try:
                ts_ms = int(r.get("_ts_ms"))
            except (TypeError, ValueError):
                continue
            msg = r.get("_message", "")
            lvl = r.get("_level",   "")
            rows.append({
                "timestamp": ts_ms,
                "message":   msg,
                "level":     _normalise_level(lvl) if lvl else _level_from_line(msg),
                "service":   r.get("_service") or "unknown",
            })
        if len(raw) < limit:
            break
        offset += len(raw)
    log.info(f"[clickhouse] {len(rows)} rows in {pages} pages")
    return rows[:max_rows]
