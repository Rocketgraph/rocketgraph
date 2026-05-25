"""
Sentry connector — project events endpoint (errors/exceptions).

credentials keys:
    token    – sntrys_eyJ... user auth token (event:read scope)  (required)
    org      – Sentry organization slug                          (required)
    project  – Sentry project slug                               (required)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

PAGE_SIZE = 100


def _link_next(link_header: str) -> Optional[str]:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part and 'results="true"' in part:
            m = re.search(r"<(.+?)>", part)
            if m:
                return m.group(1)
    return None


def _normalise_level(raw: str) -> str:
    v = (raw or "").strip().lower()
    if v in ("warning",):                       return "warn"
    if v in ("critical", "fatal"):              return "error"
    return v or "info"


def fetch_logs(credentials: dict, lookback_hours: int = 6, max_rows: int = 100_000) -> list[dict]:
    token   = credentials.get("token")
    org     = credentials.get("org")
    project = credentials.get("project")
    if not token or not org or not project:
        raise RuntimeError("Sentry requires credentials.token, org, and project")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    now     = datetime.now(timezone.utc)
    cutoff  = now - timedelta(hours=lookback_hours)

    url: Optional[str] = (
        f"https://sentry.io/api/0/projects/{org}/{project}/events/?full=true&limit={PAGE_SIZE}"
    )

    rows: list[dict] = []
    pages = 0
    while url and len(rows) < max_rows:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code in (401, 403):
            raise RuntimeError(
                "Sentry auth failed — token must be a User Auth Token (sntrys_) with event:read."
            )
        r.raise_for_status()
        events = r.json()
        pages += 1
        if not isinstance(events, list):
            break

        stop = False
        for ev in events:
            ts_str = ev.get("dateCreated") or ev.get("datetime") or ""
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < cutoff:
                stop = True
                break
            msg = (
                ev.get("title")
                or ev.get("message")
                or (ev.get("metadata") or {}).get("value")
                or ""
            )
            rows.append({
                "timestamp": int(ts.timestamp() * 1000),
                "message":   str(msg),
                "level":     _normalise_level(str(ev.get("level") or "error")),
                "service":   ev.get("culprit") or project,
            })
        if stop:
            break
        url = _link_next(r.headers.get("Link", ""))

    log.info(f"[sentry] {len(rows)} events across {pages} pages")
    return rows[:max_rows]
