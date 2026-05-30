#!/usr/bin/env bash
#
# Point Rocketgraph at a downloaded .log file and get value in one shot.
#
#   1. docker compose up --build      (starts the ML engine, mounts ./logs)
#   2. bash run.sh                     (this script)
#
# No database, no agent, no account. The file IS the analysis window.
#
set -euo pipefail

ML="${ML:-http://localhost:9020}"

echo
echo "==> Waiting for the ML engine..."
until curl -sf "$ML/health" >/dev/null 2>&1; do sleep 1; done
echo "    up."

echo
echo "==> 1. Cluster the whole log file + train the streaming detector"
echo "       (source=file — reads the file mounted at FILE_PATH)"
echo
curl -s -X POST "$ML/clusters/train?source=file" | jq '{
  log_count,
  cluster_count,
  compression: "\(.log_count) raw logs → \(.cluster_count) templates",
  anomaly_clusters: [
    .clusters[] | select(.isAnomaly == true)
    | {service, template, logCount, isolationDepth}
  ]
}'

echo
echo "==> 2. Score brand-new log lines against the trained model"
echo "       (these lines are NOT in the file — a fresh incident arriving live)"
echo
curl -s -X POST "$ML/anomalies/detect" \
  -H "Content-Type: application/json" \
  -d '{
        "logs": [
          {
            "timestamp": '"$(($(date +%s) * 1000))"',
            "service":   "payment-svc",
            "level":     "error",
            "message":   "TLS handshake failed with upstream vault-proxy after 3 retries"
          },
          {
            "timestamp": '"$(($(date +%s) * 1000))"',
            "service":   "auth-svc",
            "level":     "info",
            "message":   "User 4242 logged in from 10.0.0.1"
          }
        ]
      }' | jq '{
        scored,
        anomaly_count,
        anomalies: [.anomalies[] | {service, reasons, hst_score, message}]
      }'

echo
echo "The boring login line was dropped. The never-before-seen error came back"
echo "with reasons stacked — route 'new_template' to Jira, 'error_burst' to oncall."
echo
