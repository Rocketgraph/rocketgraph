#!/usr/bin/env bash
#
# End-to-end demo. Assumes:
#   1. docker compose up   (ClickHouse + Rocketgraph ML running)
#   2. python seed_logs.py (synthetic logs loaded into ClickHouse)
#
# Then run:  bash demo.sh
#

set -euo pipefail

ML="http://localhost:9020"

echo
echo "==> 1. Cluster the last hour and train the streaming detector"
echo
curl -s -X POST "$ML/clusters/train?source=clickhouse&window=1h" | jq '{
  log_count,
  cluster_count,
  anomalies: [.clusters[] | select(.isAnomaly == true) | {service, template, logCount, isolationDepth}]
}'

echo
echo "==> 2. Score a brand-new log against the trained model"
echo
curl -s -X POST "$ML/anomalies/detect" \
  -H "Content-Type: application/json" \
  -d '{
        "logs": [
          {
            "timestamp": '"$(($(date +%s) * 1000))"',
            "service":   "payment-svc",
            "level":     "error",
            "message":   "OOMKilled in payment-svc pod payment-9 container=stripe-worker memory=4096Mi"
          },
          {
            "timestamp": '"$(($(date +%s) * 1000))"',
            "service":   "auth-svc",
            "level":     "info",
            "message":   "User 4242 logged in from 10.0.0.1"
          }
        ]
      }' | jq

echo
echo "==> 3. Fetch+score the most recent 5 minutes straight from ClickHouse"
echo
curl -s -X POST "$ML/anomalies/detect" \
  -H "Content-Type: application/json" \
  -d '{"source": "clickhouse", "window": "custom", "hours": 1}' | jq '{
    source, scored, anomaly_count,
    sample: (.anomalies[0:3])
  }'
