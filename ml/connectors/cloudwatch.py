"""
AWS CloudWatch Logs connector — boto3 filter_log_events with nextToken pagination.

credentials keys:
    access_key_id      – AWS access key id      (required)
    secret_access_key  – AWS secret             (required)
    region             – e.g. us-east-1         (default: us-east-1)
    log_group          – /aws/lambda/my-fn      (required)
    log_stream         – optional, narrow to a single stream
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

PAGE_SIZE = 10_000

_LEVEL_RE = re.compile(
    r'\b(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|CRITICAL|FATAL|SEVERE)\b', re.I
)
_LEVEL_NORM = {
    "trace": "debug", "notice": "info", "warning": "warn",
    "critical": "error", "fatal": "error", "severe": "error",
}


def _level_from(msg: str) -> str:
    m = _LEVEL_RE.search(msg)
    if not m:
        return "info"
    v = m.group(1).lower()
    return _LEVEL_NORM.get(v, v)


def _stream_to_service(stream_name: str) -> str:
    m = re.match(r'^/?(ecs|aws)/([^/]+)', stream_name)
    if m:
        return m.group(2)
    parts = stream_name.lstrip("/").split("/")
    return parts[0][:40] if parts else stream_name[:40]


def fetch_logs(credentials: dict, lookback_hours: int = 6, max_rows: int = 100_000) -> list[dict]:
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("boto3 is required for the CloudWatch connector — pip install boto3") from e

    access_key_id     = credentials.get("access_key_id")
    secret_access_key = credentials.get("secret_access_key")
    region            = credentials.get("region") or "us-east-1"
    log_group         = credentials.get("log_group")
    log_stream        = credentials.get("log_stream") or None
    if not access_key_id or not secret_access_key or not log_group:
        raise RuntimeError(
            "CloudWatch requires credentials.access_key_id, secret_access_key, and log_group"
        )

    client = boto3.client(
        "logs",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )

    now      = datetime.now(timezone.utc)
    start    = now - timedelta(hours=lookback_hours)
    start_ms = int(start.timestamp() * 1000)
    end_ms   = int(now.timestamp() * 1000)

    rows: list[dict]      = []
    token: Optional[str]  = None
    pages = 0
    while len(rows) < max_rows:
        kwargs = {
            "logGroupName": log_group,
            "startTime":    start_ms,
            "endTime":      end_ms,
            "limit":        PAGE_SIZE,
        }
        if log_stream:
            kwargs["logStreamNames"] = [log_stream]
        if token:
            kwargs["nextToken"] = token

        resp   = client.filter_log_events(**kwargs)
        events = resp.get("events", [])
        token  = resp.get("nextToken")
        for ev in events:
            msg    = str(ev.get("message") or "")
            stream = ev.get("logStreamName", "unknown")
            rows.append({
                "timestamp": int(ev.get("timestamp", 0)),
                "message":   msg,
                "level":     _level_from(msg),
                "service":   _stream_to_service(stream),
            })
        pages += 1
        if not token:
            break

    seen, out = set(), []
    for r in rows:
        key = (r["timestamp"], r["message"][:80])
        if key not in seen:
            seen.add(key)
            out.append(r)
    log.info(f"[cloudwatch] {len(out)} rows across {pages} pages")
    return out[:max_rows]
