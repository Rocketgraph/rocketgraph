#!/usr/bin/env python3
"""
Generate a realistic sample log file so you can try the file connector without
your own data. Mimics a Datadog log export: newline-delimited JSON, one object
per line, fields under an `attributes` block — exactly what you get from
Datadog's Logs API / Log Forwarder.

~15k boring-but-normal lines across three services, plus a 2-minute incident
hiding at the end (an OOM burst and a brand-new "database failover" template the
model has never seen). Run it, then `docker compose up` and `bash run.sh`.

    python gen_sample_log.py            # → ./logs/app.log  (Datadog JSON)
    python gen_sample_log.py --format csv
    python gen_sample_log.py --format text

Already have your own export? Skip this and just drop it at ./logs/app.log.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)

NORMAL_SERVICES = ["auth-svc", "checkout-svc", "payment-svc"]


def normal_message(svc: str) -> str:
    if svc == "auth-svc":
        return random.choice([
            f"User {random.randint(1000, 9999)} logged in from 10.0.{random.randint(0,255)}.{random.randint(0,255)}",
            f"Token refreshed for session {random.randint(100000, 999999)}",
            f"Password check passed for user {random.randint(1000, 9999)}",
        ])
    if svc == "checkout-svc":
        return random.choice([
            f"GET /api/v1/cart/{random.randint(1, 5000)} 200 {random.randint(8, 95)}ms",
            f"cache hit for key product:{random.randint(1, 999)}",
            f"Order {random.randint(10000, 99999)} created with {random.randint(1, 8)} items",
        ])
    return random.choice([  # payment-svc
        f"Charge {random.randint(100000, 999999)} authorized for ${random.randint(5, 500)}.{random.randint(0,99):02d}",
        f"POST /api/v1/payments 201 {random.randint(40, 220)}ms",
        f"Stripe webhook processed event evt_{random.randint(100000, 999999)}",
    ])


def build_rows():
    now = datetime.now(timezone.utc)
    rows = []  # (ts_dt, service, level, message)

    # ── ~15k normal logs spread across the last hour ──
    for i in range(15000):
        ts = now - timedelta(minutes=60) + timedelta(milliseconds=i * 240)
        svc = random.choice(NORMAL_SERVICES)
        rows.append((ts, svc, "info", normal_message(svc)))

    # ── injected incident in the last 2 minutes ──
    # 180 OOMKilled errors in payment-svc
    for i in range(180):
        ts = now - timedelta(minutes=2) + timedelta(milliseconds=i * 600)
        rows.append((ts, "payment-svc", "error",
                     f"OOMKilled in payment-svc pod payment-{random.randint(1,9)} "
                     f"container=stripe-worker memory=4096Mi"))
    # 8 lines of a brand-new template the model has never seen
    for i in range(8):
        ts = now - timedelta(minutes=1) + timedelta(seconds=i * 5)
        rows.append((ts, "payment-svc", "error",
                     f"Database failover: replica db-replica-{i} promoted to primary "
                     f"after primary heartbeat lost"))

    rows.sort(key=lambda r: r[0])
    return rows


def write_json(rows, path: Path):
    with path.open("w", encoding="utf-8") as f:
        for ts, svc, level, msg in rows:
            f.write(json.dumps({"attributes": {
                "timestamp": ts.isoformat(),
                "status":    level,
                "service":   svc,
                "message":   msg,
            }}) + "\n")


def write_csv(rows, path: Path):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Service", "Status", "Message"])
        for ts, svc, level, msg in rows:
            w.writerow([ts.isoformat(), svc, level, msg])


def write_text(rows, path: Path):
    with path.open("w", encoding="utf-8") as f:
        for ts, svc, level, msg in rows:
            f.write(f"{ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ')} {level.upper()} "
                    f"service={svc} {msg}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["json", "csv", "text"], default="json")
    ap.add_argument("--out", default=str(Path(__file__).parent / "logs" / "app.log"))
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    {"json": write_json, "csv": write_csv, "text": write_text}[args.format](rows, out)

    print(f"Wrote {len(rows):,} log lines → {out}  (format={args.format})")
    print("Now run:  docker compose up --build   then   bash run.sh")


if __name__ == "__main__":
    main()
