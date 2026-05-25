"""
Settings loaded from environment / .env file, with a runtime override layer.

Two layers:
  1. Base — pydantic-settings reads .env once at startup.
  2. Override — POST /credentials patches values for the current process.

`credentials_for(source)` returns the merged credentials dict that connectors
consume. Override values take precedence over .env values.
"""

from __future__ import annotations

import threading
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── New Relic ─────────────────────────────────────────────────────────────
    nr_api_key:    str = ""
    nr_account_id: str = ""

    # ── Loki ──────────────────────────────────────────────────────────────────
    loki_url:     str = ""
    loki_api_key: str = ""
    loki_query:   str = '{service_name=~".+"}'

    # ── Datadog ───────────────────────────────────────────────────────────────
    dd_api_key: str = ""
    dd_app_key: str = ""
    dd_site:    str = "datadoghq.com"
    dd_query:   str = "*"

    # ── CloudWatch ────────────────────────────────────────────────────────────
    cw_access_key_id:     str = ""
    cw_secret_access_key: str = ""
    cw_region:            str = "us-east-1"
    cw_log_group:         str = ""
    cw_log_stream:        str = ""

    # ── Sentry ────────────────────────────────────────────────────────────────
    sentry_auth_token: str = ""
    sentry_org:        str = ""
    sentry_project:    str = ""

    # ── ClickHouse ────────────────────────────────────────────────────────────
    ch_url:            str = ""
    ch_user:           str = "default"
    ch_password:       str = ""
    ch_database:       str = "default"
    ch_table:          str = "logs"
    ch_col_timestamp:  str = "timestamp"
    ch_col_message:    str = "message"
    ch_col_level:      str = "level"
    ch_col_service:    str = "service"

    # ── ML knobs ──────────────────────────────────────────────────────────────
    drain_sim_th:          float = 0.4
    anomaly_contamination: float = 0.1
    hst_threshold:         float = 0.7
    default_lookback_hours: int  = 6
    max_rows:               int  = 100_000


settings = Settings()


# Mapping: source → list of (credential_field_name, settings_attr_name)
_CRED_FIELDS = {
    "newrelic": [
        ("api_key",    "nr_api_key"),
        ("account_id", "nr_account_id"),
    ],
    "loki": [
        ("url",     "loki_url"),
        ("api_key", "loki_api_key"),
        ("query",   "loki_query"),
    ],
    "datadog": [
        ("api_key", "dd_api_key"),
        ("app_key", "dd_app_key"),
        ("site",    "dd_site"),
        ("query",   "dd_query"),
    ],
    "cloudwatch": [
        ("access_key_id",     "cw_access_key_id"),
        ("secret_access_key", "cw_secret_access_key"),
        ("region",            "cw_region"),
        ("log_group",         "cw_log_group"),
        ("log_stream",        "cw_log_stream"),
    ],
    "sentry": [
        ("token",   "sentry_auth_token"),
        ("org",     "sentry_org"),
        ("project", "sentry_project"),
    ],
    "clickhouse": [
        ("url",            "ch_url"),
        ("user",           "ch_user"),
        ("password",       "ch_password"),
        ("database",       "ch_database"),
        ("table",          "ch_table"),
        ("col_timestamp",  "ch_col_timestamp"),
        ("col_message",    "ch_col_message"),
        ("col_level",      "ch_col_level"),
        ("col_service",    "ch_col_service"),
    ],
}


# Runtime overrides set by POST /credentials. {source: {field: value}}
_OVERRIDES: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def set_credentials(source: str, creds: dict[str, Any]) -> dict[str, Any]:
    if source not in _CRED_FIELDS:
        raise ValueError(f"Unknown source {source!r}. Known: {sorted(_CRED_FIELDS.keys())}")
    with _LOCK:
        existing = _OVERRIDES.setdefault(source, {})
        for k, v in creds.items():
            if v is None or v == "":
                continue
            existing[k] = v
    return credentials_for(source)


def clear_credentials(source: str | None = None) -> None:
    with _LOCK:
        if source is None:
            _OVERRIDES.clear()
        else:
            _OVERRIDES.pop(source, None)


def credentials_for(source: str) -> dict[str, Any]:
    """Merge env-loaded settings + runtime overrides into a credentials dict."""
    if source not in _CRED_FIELDS:
        raise ValueError(f"Unknown source {source!r}")
    out: dict[str, Any] = {}
    for field, attr in _CRED_FIELDS[source]:
        v = getattr(settings, attr, "")
        if v:
            out[field] = v
    with _LOCK:
        for k, v in _OVERRIDES.get(source, {}).items():
            out[k] = v
    return out


def configured_sources() -> dict[str, bool]:
    """True when a source has the minimum required credentials present."""
    required = {
        "newrelic":   ["api_key", "account_id"],
        "loki":       ["url", "api_key"],
        "datadog":    ["api_key", "app_key"],
        "cloudwatch": ["access_key_id", "secret_access_key", "log_group"],
        "sentry":     ["token", "org", "project"],
        "clickhouse": ["url"],
    }
    out: dict[str, bool] = {}
    for src, fields in required.items():
        creds = credentials_for(src)
        out[src] = all(creds.get(f) for f in fields)
    return out


def lookback_from_window(window: str | None, custom_hours: int | None) -> int:
    """
    Resolve a lookback in hours from the API window flag.

    window:
      "1h"          → 1
      "1d" / "24h"  → 24
      "7d"          → 168
      "custom"      → use custom_hours (must be > 0)
      None          → settings.default_lookback_hours
    """
    if not window:
        return settings.default_lookback_hours
    w = window.strip().lower()
    presets = {"1h": 1, "6h": 6, "12h": 12, "24h": 24, "1d": 24, "7d": 168}
    if w in presets:
        return presets[w]
    if w == "custom":
        if not custom_hours or custom_hours <= 0:
            raise ValueError("window=custom requires hours>0")
        return custom_hours
    # Allow bare integer hours like "3" or "48"
    try:
        h = int(w.rstrip("h"))
        if h > 0:
            return h
    except ValueError:
        pass
    raise ValueError(f"Unknown window {window!r}. Use 1h | 6h | 12h | 24h | 1d | 7d | custom")
