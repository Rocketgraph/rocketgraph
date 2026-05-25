"""
New Relic log connector — NRQL via the NerdGraph endpoint.

credentials keys:
    api_key     – NRAK-... User API key  (required)
    account_id  – numeric account id     (required)
    query       – NRQL projection clause, default selects service/level/message
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

NR_URL    = "https://api.newrelic.com/graphql"
PAGE_SIZE = 2000

DEFAULT_QUERY = (
    "SELECT timestamp, level, message, `service.name` AS service FROM Log"
)


def _post(nrql: str, api_key: str, account_id: str) -> list[dict]:
    body = {
        "query": (
            "{ actor { account(id: %s) { nrql(query: \"%s\") { results } } } }"
            % (account_id, nrql.replace('"', '\\"'))
        )
    }
    r = requests.post(
        NR_URL,
        headers={"Content-Type": "application/json", "API-Key": api_key},
        json=body,
        timeout=30,
    )
    if r.status_code in (401, 403):
        raise RuntimeError(
            "New Relic auth failed — set api_key=NRAK-... and account_id correctly."
        )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"New Relic query error: {data['errors']}")
    return data["data"]["actor"]["account"]["nrql"]["results"]


def fetch_logs(credentials: dict, lookback_hours: int = 6, max_rows: int = 100_000) -> list[dict]:
    api_key    = credentials.get("api_key")
    account_id = credentials.get("account_id")
    base_query = credentials.get("query") or DEFAULT_QUERY
    if not api_key or not account_id:
        raise RuntimeError("New Relic requires credentials.api_key and credentials.account_id")

    base_query = re.sub(r"\bLIMIT\s+\S+", "", base_query, flags=re.I).strip()
    base_query = re.sub(r"\bSINCE\b.*$", "", base_query, flags=re.I).strip()

    lookback_seconds = lookback_hours * 3600
    window_seconds   = max(math.floor((lookback_seconds * PAGE_SIZE) / max_rows), 1)
    n_windows        = math.ceil(lookback_seconds / window_seconds)

    log.info(
        f"[newrelic] {lookback_hours}h lookback → {n_windows} windows of {window_seconds}s"
    )

    now   = datetime.now(timezone.utc)
    start = now - timedelta(hours=lookback_hours)
    rows: list[dict] = []

    for i in range(n_windows):
        if len(rows) >= max_rows:
            break
        s = start + timedelta(seconds=i * window_seconds)
        e = start + timedelta(seconds=(i + 1) * window_seconds)
        q = (
            f"{base_query} "
            f"SINCE '{s.strftime('%Y-%m-%d %H:%M:%S+00:00')}' "
            f"UNTIL '{e.strftime('%Y-%m-%d %H:%M:%S+00:00')}' "
            f"LIMIT {PAGE_SIZE}"
        )
        rows.extend(_post(q, api_key, account_id))

    # Deduplicate across window boundaries
    seen, out = set(), []
    for r in rows:
        key = (r.get("timestamp"), str(r.get("message", ""))[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "timestamp": int(r.get("timestamp") or 0),
            "message":   str(r.get("message") or ""),
            "level":     str(r.get("level")   or "info").lower(),
            "service":   str(r.get("service") or "unknown"),
        })
    return out[:max_rows]
