"""
Rocketgraph ML — Drain3 clustering + Half-Space-Trees anomaly detection.

Endpoints
─────────
  GET  /clusters
         ?source=loki|newrelic|datadog|cloudwatch|sentry|clickhouse|file
         &window=1h|6h|12h|24h|1d|7d|custom
         &hours=<int>   (when window=custom)
         → Drain3 templates + Isolation-Forest anomaly scores per cluster.

  POST /clusters/train
         ?source=…  &window=…
         → Same as /clusters AND trains a per-service HalfSpaceTrees model on
           the fetched window so /anomalies/detect can score new logs.

  POST /anomalies/detect
         body: {"logs": [{"timestamp": ms, "message": "...", "level": "...",
                          "service": "..."}, ...]}
         → Scores each log against the trained HST. Returns only anomalies.
         (You can also pass {"source": "loki", "window": "1h"} to fetch+score
          from a connector instead of providing logs inline.)

Aux endpoints
─────────────
  POST /credentials     → set/override connector credentials at runtime
  GET  /credentials     → which sources are currently configured
  GET  /sources         → list of supported sources
  GET  /detector/status → HST training status
  POST /detector/reset  → wipe the trained HST
  GET  /health
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import config as cfg
import connectors
from core.drain import cluster_logs
from core.hst import StreamDetector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
log = logging.getLogger("rocketgraph.ml")

app = FastAPI(
    title="Rocketgraph ML",
    description="Open-source log clustering (Drain3 + Isolation Forest) and streaming anomaly detection (Half-Space-Trees).",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global HST detector. Re-created on /detector/reset.
_detector = StreamDetector(
    sim_th=cfg.settings.drain_sim_th,
    hst_threshold=cfg.settings.hst_threshold,
)


def _resolve_lookback(window: str | None, hours: int | None) -> int:
    try:
        return cfg.lookback_from_window(window, hours)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _parse_iso(label: str, value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid {label}={value!r}: {e}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_window(
    source:  str,
    window:  str | None,
    hours:   int | None,
    start:   str | None,
    end:     str | None,
) -> tuple[int, datetime | None, datetime | None]:
    """
    Returns (lookback_hours, start_dt, end_dt).
    When start/end are provided, lookback is derived from their delta and the
    absolute datetimes are returned. Connectors without absolute-window support
    will fall back to using lookback_hours from now().
    """
    if start or end:
        if not connectors.supports_absolute_window(source):
            raise HTTPException(
                status_code=400,
                detail=f"Source {source!r} does not yet accept absolute start/end. Use 'window' instead.",
            )
        end_dt   = _parse_iso("end",   end)   if end   else datetime.now(timezone.utc)
        start_dt = _parse_iso("start", start) if start else end_dt
        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="start must be before end")
        lookback = max(int((end_dt - start_dt).total_seconds() // 3600), 1)
        return lookback, start_dt, end_dt
    return _resolve_lookback(window, hours), None, None


def _fetch(
    source:    str,
    lookback_hours: int,
    start_dt:  datetime | None = None,
    end_dt:    datetime | None = None,
) -> list[dict]:
    if source not in connectors.REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source {source!r}. Known: {connectors.available()}",
        )
    try:
        creds = cfg.credentials_for(source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        return connectors.fetch(
            source,
            credentials=creds,
            lookback_hours=lookback_hours,
            max_rows=cfg.settings.max_rows,
            start=start_dt,
            end=end_dt,
        )
    except RuntimeError as e:
        # Auth / connector configuration errors
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception(f"[{source}] fetch failed")
        raise HTTPException(status_code=502, detail=f"{source} fetch failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# /clusters — Drain3 + Isolation Forest
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/clusters", summary="Drain3 clusters + isolation-forest anomalies")
def get_clusters(
    source: str        = Query(..., description="loki | newrelic | datadog | cloudwatch | sentry | clickhouse | file"),
    window: str        = Query("6h", description="1h | 6h | 12h | 24h | 1d | 7d | custom"),
    hours:  int | None = Query(None, description="Custom lookback in hours (when window=custom)"),
    start:  str | None = Query(None, description="Absolute ISO-8601 start (UTC). Overrides window. ClickHouse only."),
    end:    str | None = Query(None, description="Absolute ISO-8601 end   (UTC). Overrides window. ClickHouse only."),
):
    lookback, start_dt, end_dt = _resolve_window(source, window, hours, start, end)
    rows = _fetch(source, lookback, start_dt, end_dt)
    clusters, _df = cluster_logs(
        rows,
        sim_th=cfg.settings.drain_sim_th,
        contamination=cfg.settings.anomaly_contamination,
    )
    return {
        "source":          source,
        "window":          window if not (start or end) else "absolute",
        "lookback_hours":  lookback,
        "start":           start_dt.isoformat() if start_dt else None,
        "end":             end_dt.isoformat()   if end_dt   else None,
        "log_count":       len(rows),
        "cluster_count":   len(clusters),
        "clusters":        clusters,
    }


# ──────────────────────────────────────────────────────────────────────────────
# /clusters/train — Drain3 + IF + warm-up HST
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/clusters/train", summary="Cluster + train the streaming HST detector")
def train_clusters(
    source: str        = Query(...),
    window: str        = Query("6h"),
    hours:  int | None = Query(None),
    start:  str | None = Query(None, description="Absolute ISO-8601 start (UTC). ClickHouse only."),
    end:    str | None = Query(None, description="Absolute ISO-8601 end   (UTC). ClickHouse only."),
):
    lookback, start_dt, end_dt = _resolve_window(source, window, hours, start, end)
    rows = _fetch(source, lookback, start_dt, end_dt)
    clusters, _df = cluster_logs(
        rows,
        sim_th=cfg.settings.drain_sim_th,
        contamination=cfg.settings.anomaly_contamination,
    )
    n_trained = _detector.train(rows)
    return {
        "source":          source,
        "window":          window if not (start or end) else "absolute",
        "lookback_hours":  lookback,
        "start":           start_dt.isoformat() if start_dt else None,
        "end":             end_dt.isoformat()   if end_dt   else None,
        "log_count":       len(rows),
        "cluster_count":   len(clusters),
        "trained_on":      n_trained,
        "detector":        _detector.status(),
        "clusters":        clusters,
    }


# ──────────────────────────────────────────────────────────────────────────────
# /anomalies/detect — HST scoring
# ──────────────────────────────────────────────────────────────────────────────

class LogIn(BaseModel):
    timestamp: int    = Field(..., description="Epoch milliseconds")
    message:   str
    level:     str    = "info"
    service:   str    = "unknown"


class DetectRequest(BaseModel):
    logs:   list[LogIn] | None = None
    source: str | None         = None
    window: str | None         = None
    hours:  int | None         = None


@app.post("/anomalies/detect", summary="Score logs against the trained HST detector")
def detect_anomalies(req: DetectRequest = Body(...)):
    if _detector.trained_at is None:
        raise HTTPException(
            status_code=409,
            detail="Detector is not trained. Call POST /clusters/train?source=… first.",
        )

    if req.logs:
        rows = [r.model_dump() for r in req.logs]
        source = "inline"
        lookback = None
    elif req.source:
        lookback = _resolve_lookback(req.window or "1h", req.hours)
        rows     = _fetch(req.source, lookback)
        source   = req.source
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'logs' (inline) or 'source' (to fetch from a connector).",
        )

    anomalies = _detector.score(rows)
    return {
        "source":        source,
        "lookback_hours": lookback,
        "scored":        len(rows),
        "anomaly_count": len(anomalies),
        "anomalies":     anomalies,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Credentials — dynamic override
# ──────────────────────────────────────────────────────────────────────────────

class CredentialsIn(BaseModel):
    source: str
    credentials: dict[str, Any]


@app.post("/credentials", summary="Set or override connector credentials at runtime")
def set_credentials_endpoint(req: CredentialsIn = Body(...)):
    try:
        merged = cfg.set_credentials(req.source, req.credentials)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "source": req.source,
        "fields_set": sorted(req.credentials.keys()),
        "configured": cfg.configured_sources()[req.source],
        "merged_keys": sorted(merged.keys()),
    }


@app.delete("/credentials", summary="Clear runtime credential overrides")
def clear_credentials_endpoint(source: str | None = Query(None)):
    cfg.clear_credentials(source)
    return {"cleared": source or "all"}


@app.get("/credentials", summary="Which sources are currently configured")
def list_credentials():
    return {
        "sources":    connectors.available(),
        "configured": cfg.configured_sources(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Misc
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/sources")
def sources():
    return {"sources": connectors.available()}


@app.get("/detector/status")
def detector_status():
    return _detector.status()


@app.post("/detector/reset")
def detector_reset():
    global _detector
    _detector = StreamDetector(
        sim_th=cfg.settings.drain_sim_th,
        hst_threshold=cfg.settings.hst_threshold,
    )
    return {"status": "reset", "detector": _detector.status()}


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
