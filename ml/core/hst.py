"""
Half-Space-Trees streaming anomaly detector — one model per service.

After training on a batch of logs, each ServiceDetector keeps state and can
score new logs as they arrive. Three signals fire an anomaly:

  1. HST score    — feature vector unusual for this service
  2. New template — Drain has never seen this template for this service
  3. Error burst  — >threshold errors in the last N seconds for this service
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Iterable

from river.anomaly import HalfSpaceTrees

from .drain import new_template_miner
from .mask import mask


class ServiceDetector:
    """Per-service HST + Drain state. Fully isolated."""

    def __init__(
        self,
        service: str,
        sim_th: float = 0.4,
        hst_threshold: float = 0.7,
        burst_window_s: int = 60,
        burst_threshold: float = 0.6,
    ):
        self.service         = service
        self.hst             = HalfSpaceTrees(n_trees=25, height=8, window_size=250, seed=42)
        self.drain           = new_template_miner(sim_th=sim_th)
        self.template_counts: dict[int, int] = defaultdict(int)
        self.recent_levels:   deque          = deque()

        self.hst_threshold   = hst_threshold
        self.burst_window_s  = burst_window_s
        self.burst_threshold = burst_threshold

    def _featurize(self, message: str, level: str, template_id: int) -> dict:
        return {
            "is_error":        float(level == "error"),
            "is_warn":         float(level == "warn"),
            "msg_len":         min(len(message), 500) / 500.0,
            "token_count":     min(len(message.split()), 50) / 50.0,
            "template_rarity": 1.0 / (1.0 + self.template_counts[template_id]),
        }

    def learn(self, message: str, level: str, ts_ms: int) -> None:
        """Update state without producing a verdict — used for warm-up."""
        r           = self.drain.add_log_message(mask(message))
        template_id = r["cluster_id"]
        self.template_counts[template_id] += 1
        self.hst.learn_one(self._featurize(message, level, template_id))

    def score(self, message: str, level: str, ts_ms: int) -> dict:
        """Score one log. Returns a verdict dict (or {} when not anomalous)."""
        r            = self.drain.add_log_message(mask(message))
        template     = r["template_mined"]
        template_id  = r["cluster_id"]
        is_new       = self.template_counts[template_id] == 0
        self.template_counts[template_id] += 1

        features = self._featurize(message, level, template_id)
        hst_score = self.hst.score_one(features)
        self.hst.learn_one(features)

        cutoff = ts_ms - self.burst_window_s * 1000
        self.recent_levels.append((ts_ms, level == "error"))
        while self.recent_levels and self.recent_levels[0][0] < cutoff:
            self.recent_levels.popleft()
        in_burst = (
            len(self.recent_levels) >= 5
            and sum(e for _, e in self.recent_levels) / len(self.recent_levels)
                >= self.burst_threshold
        )

        reasons: list[str] = []
        if hst_score >= self.hst_threshold:
            reasons.append("anomaly_score")
        if is_new and level in ("error", "warn"):
            reasons.append("new_template")
        if in_burst and level == "error":
            reasons.append("error_burst")

        return {
            "is_anomaly":   bool(reasons),
            "reasons":      reasons,
            "template":     template,
            "template_id":  template_id,
            "hst_score":    round(float(hst_score), 4),
            "service":      self.service,
        }


class StreamDetector:
    """Holds one ServiceDetector per service and routes logs."""

    def __init__(self, sim_th: float = 0.4, hst_threshold: float = 0.7):
        self.sim_th        = sim_th
        self.hst_threshold = hst_threshold
        self._services: dict[str, ServiceDetector] = {}
        self.trained_at: datetime | None = None
        self.training_log_count: int     = 0

    def _detector(self, service: str) -> ServiceDetector:
        if service not in self._services:
            self._services[service] = ServiceDetector(
                service,
                sim_th=self.sim_th,
                hst_threshold=self.hst_threshold,
            )
        return self._services[service]

    def train(self, rows: Iterable[dict]) -> int:
        """Feed historical logs through Drain + HST without raising anomalies."""
        n = 0
        for row in rows:
            message = str(row.get("message") or "")
            level   = str(row.get("level")   or "info").lower()
            ts_ms   = int(row.get("timestamp") or 0)
            service = str(row.get("service")  or "unknown")
            self._detector(service).learn(message, level, ts_ms)
            n += 1
        self.training_log_count += n
        self.trained_at = datetime.now(timezone.utc)
        return n

    def score(self, rows: Iterable[dict]) -> list[dict]:
        """Score a batch of logs. Returns only the anomalous ones."""
        out: list[dict] = []
        for row in rows:
            message = str(row.get("message") or "")
            level   = str(row.get("level")   or "info").lower()
            ts_ms   = int(row.get("timestamp") or 0)
            service = str(row.get("service")  or "unknown")

            verdict = self._detector(service).score(message, level, ts_ms)
            if verdict["is_anomaly"]:
                verdict["timestamp"] = datetime.fromtimestamp(
                    ts_ms / 1000, tz=timezone.utc
                ).isoformat() if ts_ms else None
                verdict["message"]   = message
                verdict["level"]     = level
                out.append(verdict)
        return out

    def status(self) -> dict:
        return {
            "trained":            self.trained_at is not None,
            "trained_at":         self.trained_at.isoformat() if self.trained_at else None,
            "training_log_count": self.training_log_count,
            "services_tracked":   sorted(self._services.keys()),
            "templates_seen":     sum(len(d.template_counts) for d in self._services.values()),
        }
