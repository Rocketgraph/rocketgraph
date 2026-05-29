"""
Generates a fake but realistic log stream and loads it into ClickHouse.

The stream has three services and looks roughly like a real production hour:
  - 95% boring INFO traffic (logins, GETs, cache hits, queue acks)
  - a small steady trickle of expected WARN/ERROR
  - one injected incident: a burst of OOMKilled errors in payment-svc,
    plus a brand-new template ("Database failover: replica %s promoted")
    that the model has never seen before

Run AFTER docker compose is up:

    python seed_logs.py
"""

from __future__ import annotations

import json
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

CH_URL = "http://localhost:8123"
DB = "demo"
TABLE = "logs"

random.seed(42)

NOW = datetime.now(timezone.utc)
WINDOW_MINUTES = 60
ROWS_PER_MINUTE = 250


SERVICES = ["auth-svc", "payment-svc", "checkout-svc"]

NORMAL_TEMPLATES = [
    ("auth-svc",     "info",  lambda: f"User {random.randint(1000, 9999)} logged in from {_ip()}"),
    ("auth-svc",     "info",  lambda: f"Issued JWT for user {random.randint(1000, 9999)}"),
    ("auth-svc",     "info",  lambda: f"GET /me 200 in {random.randint(2, 40)}ms"),
    ("payment-svc",  "info",  lambda: f"Charged ${random.randint(5, 800)}.{random.randint(0, 99):02d} for order {uuid.uuid4()}"),
    ("payment-svc",  "info",  lambda: f"Stripe webhook processed event={_event()}"),
    ("checkout-svc", "info",  lambda: f"GET /cart 200 in {random.randint(3, 25)}ms"),
    ("checkout-svc", "info",  lambda: f"Added item sku-{random.randint(100, 999)} to cart {uuid.uuid4()}"),
    ("checkout-svc", "info",  lambda: f"Cache hit for product sku-{random.randint(100, 999)}"),
]

EXPECTED_NOISE = [
    ("auth-svc",     "warn",  lambda: f"Slow login query: {random.randint(200, 900)}ms for user {random.randint(1000, 9999)}"),
    ("payment-svc",  "warn",  lambda: f"Stripe retry attempt {random.randint(1, 3)} for charge {uuid.uuid4()}"),
    ("checkout-svc", "error", lambda: f"Inventory check failed for sku-{random.randint(100, 999)}: temporary 503"),
]


def _ip() -> str:
    return f"{random.randint(10, 200)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}"


def _event() -> str:
    return random.choice(["payment_intent.succeeded", "charge.succeeded", "invoice.paid"])


def _row(ts: datetime, service: str, level: str, message: str) -> dict:
    return {
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "service":   service,
        "level":     level,
        "message":   message,
    }


def generate_normal_window() -> list[dict]:
    rows: list[dict] = []
    start = NOW - timedelta(minutes=WINDOW_MINUTES)
    for minute in range(WINDOW_MINUTES):
        ts_minute = start + timedelta(minutes=minute)
        for _ in range(ROWS_PER_MINUTE):
            offset_ms = random.randint(0, 59_999)
            ts = ts_minute + timedelta(milliseconds=offset_ms)
            if random.random() < 0.03:
                svc, lvl, mk = random.choice(EXPECTED_NOISE)
            else:
                svc, lvl, mk = random.choice(NORMAL_TEMPLATES)
            rows.append(_row(ts, svc, lvl, mk()))
    return rows


def generate_incident_burst() -> list[dict]:
    """An OOMKilled storm plus one never-before-seen template in payment-svc."""
    rows: list[dict] = []
    burst_start = NOW - timedelta(minutes=2)
    for i in range(180):
        ts = burst_start + timedelta(milliseconds=i * 600)
        rows.append(_row(
            ts, "payment-svc", "error",
            f"OOMKilled in payment-svc pod payment-{random.randint(1, 8)} container=stripe-worker memory=4096Mi",
        ))
    for i in range(8):
        ts = burst_start + timedelta(seconds=i * 7)
        rows.append(_row(
            ts, "payment-svc", "error",
            f"Database failover: replica db-replica-{random.randint(1, 3)} promoted to primary after primary heartbeat lost",
        ))
    return rows


def insert(rows: list[dict]) -> None:
    payload = "\n".join(json.dumps(r) for r in rows)
    r = requests.post(
        f"{CH_URL}/?query=INSERT%20INTO%20{DB}.{TABLE}%20FORMAT%20JSONEachRow",
        data=payload.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        timeout=120,
    )
    r.raise_for_status()


def wait_for_clickhouse() -> None:
    for _ in range(60):
        try:
            r = requests.get(f"{CH_URL}/?query=SELECT%201", timeout=2)
            if r.ok and r.text.strip() == "1":
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("ClickHouse did not come up in 60s. Did you run `docker compose up`?")


def main() -> None:
    wait_for_clickhouse()
    normal = generate_normal_window()
    incident = generate_incident_burst()
    print(f"Inserting {len(normal):,} normal rows ...")
    insert(normal)
    print(f"Inserting {len(incident):,} incident-burst rows ...")
    insert(incident)
    print(f"Done. Total rows in demo.logs: {len(normal) + len(incident):,}")


if __name__ == "__main__":
    main()
