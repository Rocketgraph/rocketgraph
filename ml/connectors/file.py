"""
File connector — point Rocketgraph at a `.log` file on disk and get value with
zero infrastructure. Built for the "download your logs and run it" workflow:
export from Datadog (or anything else), drop the file next to the engine, done.

It auto-detects the three shapes a downloaded log file usually arrives in:

  1. Datadog JSON export — one JSON object per line (NDJSON), with fields under
     a top-level `attributes` block: {"attributes": {"timestamp", "message",
     "status", "service", ...}}. Also handles a single JSON array of such rows.
  2. Generic NDJSON / JSON array — top-level {"message"|"msg"|"log",
     "level"|"status"|"severity", "service", "timestamp"|"date"|"@timestamp"}.
  3. CSV export — Datadog's UI "Export to CSV" and similar. A header row names
     the columns (Date / Message / Service / Status / Host …); we map them to the
     normalised shape.
  4. Plain text — raw application log lines. We best-effort sniff the leading
     timestamp, the level (info/warn/error/...), and a service tag, then hand the
     line to Drain which masks the variable bits anyway.

The whole file is treated as the analysis window — `lookback_hours` is ignored,
because a downloaded snapshot is already the slice the user cares about.

credentials keys:
    path     – path to the .log / .json / .ndjson / .csv file   (required)
    format   – auto | json | csv | text                         (default: auto)
    service  – fallback service name when a line/row has none
               (default: the file stem, e.g. "payment-svc.log" → "payment-svc")
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ── level normalisation ───────────────────────────────────────────────────────
_LEVEL_NORM = {
    "warning":  "warn",
    "err":      "error",
    "critical": "error",
    "crit":     "error",
    "fatal":    "error",
    "emerg":    "error",
    "alert":    "error",
    "trace":    "debug",
    "notice":   "info",
    "ok":       "info",
}
_LEVEL_RE = re.compile(
    r'\b(debug|info|warn(?:ing)?|error|err|crit(?:ical)?|fatal|trace)\b', re.I
)

# ── timestamp sniffing for plain-text lines ───────────────────────────────────
# Each pattern captures the timestamp text at the start of (or early in) a line.
_TS_PATTERNS = [
    # 2026-05-29T10:00:00.123Z  /  2026-05-29 10:00:00,123  /  +00:00 offsets
    re.compile(r'(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)'),
    # [2026-05-29 10:00:00]
    re.compile(r'\[(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]'),
    # epoch millis or seconds at the very start
    re.compile(r'^(?P<ts>\d{10,13})\b'),
    # syslog: May 29 10:00:00
    re.compile(r'^(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})'),
]

_SERVICE_PATTERNS = [
    re.compile(r'\bservice[=:]\s*"?([\w.\-]+)"?', re.I),
    re.compile(r'\b(?:svc|app|logger|component)[=:]\s*"?([\w.\-]+)"?', re.I),
]


def _normalise_level(raw: str) -> str:
    v = (raw or "").strip().lower()
    return _LEVEL_NORM.get(v, v) if v else "info"


def _level_from_text(line: str) -> str:
    m = _LEVEL_RE.search(line)
    if not m:
        return "info"
    return _LEVEL_NORM.get(m.group(1).lower(), m.group(1).lower())


def _to_ms(value) -> int | None:
    """Best-effort convert an int/float/str timestamp to epoch milliseconds."""
    if value is None:
        return None
    # numeric epoch
    if isinstance(value, (int, float)):
        n = float(value)
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.isdigit():
            n = float(s)
        else:
            # ISO-8601 (with or without trailing Z / offset)
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                # syslog-style "May 29 10:00:00" — no year; assume current year
                for fmt in ("%b %d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S,%f"):
                    try:
                        dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                        return int(dt.timestamp() * 1000)
                    except ValueError:
                        continue
                return None
    # numeric: decide s vs ms vs µs vs ns by magnitude
    if n >= 1e16:        # nanoseconds
        return int(n / 1e6)
    if n >= 1e13:        # microseconds
        return int(n / 1e3)
    if n >= 1e11:        # milliseconds
        return int(n)
    return int(n * 1000)  # seconds


# ── JSON row extraction (Datadog export + generic) ────────────────────────────
_MSG_KEYS   = ("message", "msg", "log", "text", "content", "body")
_LEVEL_KEYS = ("status", "level", "severity", "loglevel", "log_level", "syslog.severity")
_SVC_KEYS   = ("service", "svc", "app", "source", "logger", "ddsource")
_TS_KEYS    = ("timestamp", "date", "time", "@timestamp", "_time", "datetime")


def _dig(obj: dict, keys: tuple[str, ...]):
    """Look up the first present key at top level, then inside `attributes`."""
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    attrs = obj.get("attributes")
    if isinstance(attrs, dict):
        for k in keys:
            if k in attrs and attrs[k] not in (None, ""):
                return attrs[k]
        # Datadog nests custom fields one more level under attributes.attributes
        inner = attrs.get("attributes")
        if isinstance(inner, dict):
            for k in keys:
                if k in inner and inner[k] not in (None, ""):
                    return inner[k]
    return None


def _row_from_json(obj: dict, default_service: str) -> dict | None:
    if not isinstance(obj, dict):
        return None
    msg = _dig(obj, _MSG_KEYS)
    if msg is None:
        # nothing message-like — skip empty/metadata rows
        return None
    lvl_raw = _dig(obj, _LEVEL_KEYS)
    level   = _normalise_level(str(lvl_raw)) if lvl_raw else _level_from_text(str(msg))
    svc     = _dig(obj, _SVC_KEYS) or default_service
    ts_ms   = _to_ms(_dig(obj, _TS_KEYS))
    return {
        "timestamp": ts_ms,                 # may be None → filled in later
        "message":   str(msg),
        "level":     level,
        "service":   str(svc),
    }


def _row_from_text(line: str, default_service: str) -> dict:
    ts_ms: int | None = None
    message = line
    for pat in _TS_PATTERNS:
        m = pat.search(line)
        if m:
            ts_ms = _to_ms(m.group("ts"))
            if ts_ms is not None:
                # strip a leading timestamp so templates stay clean
                if m.start() <= 1:
                    message = line[m.end():].lstrip(" -|]\t")
                break
    service = default_service
    for pat in _SERVICE_PATTERNS:
        sm = pat.search(line)
        if sm:
            service = sm.group(1)
            break
    return {
        "timestamp": ts_ms,
        "message":   message or line,
        "level":     _level_from_text(line),
        "service":   service,
    }


def _detect_format(sample_lines: list[str]) -> str:
    for ln in sample_lines:
        s = ln.strip()
        if not s:
            continue
        if s[0] in "[{":
            return "json"
        # CSV: a comma-separated header naming at least one known column
        if "," in s:
            header = {h.strip().strip('"').lower() for h in s.split(",")}
            known = set(_MSG_KEYS) | set(_LEVEL_KEYS) | set(_SVC_KEYS) | set(_TS_KEYS)
            if header & known:
                return "csv"
        return "text"
    return "text"


def _parse_csv(text: str, default_service: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    # case-insensitive column lookup
    lower_map = {(name or "").strip().lower(): name for name in reader.fieldnames}

    def pick(keys: tuple[str, ...]):
        for k in keys:
            if k in lower_map:
                return lower_map[k]
        return None

    col_msg = pick(_MSG_KEYS)
    col_lvl = pick(_LEVEL_KEYS)
    col_svc = pick(_SVC_KEYS)
    col_ts  = pick(_TS_KEYS)

    rows: list[dict] = []
    for rec in reader:
        msg = (rec.get(col_msg) if col_msg else None)
        if not msg:
            # no dedicated message column — join the row so Drain still sees content
            msg = " ".join(str(v) for v in rec.values() if v)
        if not msg:
            continue
        lvl_raw = rec.get(col_lvl) if col_lvl else None
        level   = _normalise_level(str(lvl_raw)) if lvl_raw else _level_from_text(str(msg))
        svc     = (rec.get(col_svc) if col_svc else None) or default_service
        ts_ms   = _to_ms(rec.get(col_ts)) if col_ts else None
        rows.append({
            "timestamp": ts_ms,
            "message":   str(msg),
            "level":     level,
            "service":   str(svc),
        })
    return rows


def _parse_json(text: str, default_service: str) -> list[dict]:
    """Handle both a single JSON array and newline-delimited JSON."""
    rows: list[dict] = []
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
            if isinstance(arr, list):
                for obj in arr:
                    r = _row_from_json(obj, default_service)
                    if r:
                        rows.append(r)
                return rows
        except json.JSONDecodeError:
            pass  # fall through to line-by-line
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        r = _row_from_json(obj, default_service)
        if r:
            rows.append(r)
    return rows


def _fill_timestamps(rows: list[dict]) -> None:
    """Ensure every row has an epoch-ms timestamp so HST burst/first-seen logic
    works. If most rows lack one, synthesise a monotonic stream ending ~now so
    ordering (and therefore error bursts) is preserved. Otherwise forward-fill
    the gaps from the nearest known timestamp."""
    if not rows:
        return
    have = [i for i, r in enumerate(rows) if r["timestamp"] is not None]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    if len(have) < len(rows) * 0.5:
        base = now_ms - len(rows)
        for i, r in enumerate(rows):
            r["timestamp"] = base + i
        return

    # forward-fill, then back-fill any leading gap
    last = None
    for r in rows:
        if r["timestamp"] is None:
            r["timestamp"] = last if last is not None else now_ms
        else:
            last = r["timestamp"]
    first_known = rows[have[0]]["timestamp"]
    for i in range(have[0]):
        rows[i]["timestamp"] = first_known


def fetch_logs(credentials: dict, lookback_hours: int = 6, max_rows: int = 100_000) -> list[dict]:
    path = credentials.get("path")
    if not path:
        raise RuntimeError("File connector requires credentials.path (path to the .log file)")

    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"File not found: {path}")
    if not p.is_file():
        raise RuntimeError(f"Not a file: {path}")

    fmt             = (credentials.get("format") or "auto").strip().lower()
    default_service = credentials.get("service") or p.stem or "unknown"

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise RuntimeError(f"Could not read {path}: {e}")

    if fmt == "auto":
        fmt = _detect_format(text.splitlines()[:20])

    if fmt == "json":
        rows = _parse_json(text, default_service)
        # a "json" file with no parseable objects → fall back to text
        if not rows:
            log.warning(f"[file] no JSON rows parsed from {path}; retrying as plain text")
            fmt = "text"
    elif fmt == "csv":
        rows = _parse_csv(text, default_service)
        if not rows:
            log.warning(f"[file] no CSV rows parsed from {path}; retrying as plain text")
            fmt = "text"
    if fmt == "text":
        rows = [
            _row_from_text(ln, default_service)
            for ln in text.splitlines()
            if ln.strip()
        ]

    _fill_timestamps(rows)
    rows = rows[:max_rows]
    log.info(f"[file] {len(rows)} rows from {path} (format={fmt})")
    return rows
