"""
Log-source connectors. Each module exports:

    fetch_logs(credentials, lookback_hours, max_rows, start=None, end=None) -> list[dict]

Connectors that don't yet accept absolute (start, end) windows simply ignore
those kwargs and use the rolling lookback. Each row is:
    {"timestamp": int_ms, "message": str, "level": str, "service": str}
"""

from datetime import datetime
from typing import Optional

from . import cloudwatch, clickhouse, datadog, file, loki, newrelic, sentry

REGISTRY = {
    "newrelic":   newrelic.fetch_logs,
    "loki":       loki.fetch_logs,
    "datadog":    datadog.fetch_logs,
    "cloudwatch": cloudwatch.fetch_logs,
    "sentry":     sentry.fetch_logs,
    "clickhouse": clickhouse.fetch_logs,
    "file":       file.fetch_logs,
}

# Connectors that accept absolute (start, end) datetimes via kwargs.
_ABSOLUTE_WINDOW_SUPPORT = {"clickhouse"}


def available() -> list[str]:
    return sorted(REGISTRY.keys())


def supports_absolute_window(source: str) -> bool:
    return source in _ABSOLUTE_WINDOW_SUPPORT


def fetch(
    source: str,
    credentials: dict,
    lookback_hours: int,
    max_rows: int,
    start: Optional[datetime] = None,
    end:   Optional[datetime] = None,
) -> list[dict]:
    if source not in REGISTRY:
        raise ValueError(f"Unknown source: {source!r}. Available: {available()}")
    kwargs = dict(credentials=credentials, lookback_hours=lookback_hours, max_rows=max_rows)
    if (start is not None or end is not None) and source in _ABSOLUTE_WINDOW_SUPPORT:
        kwargs["start"] = start
        kwargs["end"]   = end
    return REGISTRY[source](**kwargs)
