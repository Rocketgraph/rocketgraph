# Rocketgraph ML

Open-source log clustering and streaming anomaly detection.

A standalone FastAPI service that points at the monitoring tool you already use (New Relic, Loki, Datadog, CloudWatch, Sentry, ClickHouse), runs **Drain3** template mining + **Isolation Forest** anomaly scoring across the parsed templates, and trains a per-service **Half-Space-Trees** model so you can score new log lines on the fly.

No database. No accounts. Stateless except for the in-memory HST model.

## What it does

```
       ┌────────────────────────────────────────────────────┐
       │  monitoring source (NR / Loki / DD / CW / Sentry)  │
       └─────────────────────────┬──────────────────────────┘
                                 │ pull window of logs
                                 ▼
                    ┌─────────────────────────┐
                    │     mask + Drain3        │  →  templates
                    └────────────┬─────────────┘
                                 ▼
            ┌────────────────────┴────────────────────┐
            │  Isolation Forest (per service)         │  →  cluster JSON
            │  + TF-IDF + PCA 2-D coordinates         │
            └────────────────────┬────────────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  Half-Space-Trees       │  →  /anomalies/detect
                    │  (one per service)      │
                    └─────────────────────────┘
```

## Quick start

```bash
git clone https://github.com/rocketgraph/rocketgraph
cd rocketgraph/ml
cp .env.example .env             # fill in the sources you have
docker compose up --build        # → http://localhost:9020
```

Or run it directly:

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 9020
```

Health check:

```bash
curl http://localhost:9020/health
```

## Endpoints

### 1. `GET /clusters` — cluster a window of logs

```bash
curl 'http://localhost:9020/clusters?source=loki&window=1h'
```

`window` accepts: `1h`, `6h`, `12h`, `24h`, `1d`, `7d`, or `custom` with `&hours=<int>`.

Response:

```json
{
  "source": "loki",
  "window": "1h",
  "lookback_hours": 1,
  "log_count": 18234,
  "cluster_count": 87,
  "clusters": [
    {
      "id": "12",
      "name": "User <NUM> logged in from <IP>",
      "service": "auth-svc",
      "x": 32.1, "y": 71.4, "size": 24, "color": "blue",
      "logCount": 1402,
      "isAnomaly": false,
      "avgScore": 0.21,
      "firstSeen": "2026-05-25T06:01:34+00:00",
      "template": "User <NUM> logged in from <IP>",
      "logs": [ { "timestamp": "...", "message": "...", "level": "info" } ]
    },
    { "id": "73", "isAnomaly": true, "isolationDepth": 3, "color": "red", ... }
  ]
}
```

### 2. `POST /clusters/train` — cluster **and** train the live detector

```bash
curl -XPOST 'http://localhost:9020/clusters/train?source=loki&window=1d'
```

Same response as `/clusters`, plus warms the per-service Half-Space-Trees models on the same window of logs. After this call, `/anomalies/detect` is ready.

### 3. `POST /anomalies/detect` — score new logs against the trained HST

**Inline logs:**

```bash
curl -XPOST http://localhost:9020/anomalies/detect \
  -H 'Content-Type: application/json' \
  -d '{
        "logs": [
          {"timestamp": 1716624000000, "message": "OOMKilled in payment-svc", "level": "error", "service": "payment-svc"},
          {"timestamp": 1716624001000, "message": "User 123 logged in",        "level": "info",  "service": "auth-svc"}
        ]
      }'
```

**Or fetch + score from a connector in one shot:**

```bash
curl -XPOST http://localhost:9020/anomalies/detect \
  -H 'Content-Type: application/json' \
  -d '{"source": "loki", "window": "1h"}'
```

Response — only the rows flagged as anomalous are returned:

```json
{
  "source": "inline",
  "scored": 2,
  "anomaly_count": 1,
  "anomalies": [
    {
      "is_anomaly": true,
      "reasons": ["anomaly_score", "new_template"],
      "service": "payment-svc",
      "template": "OOMKilled in <SERVICE>",
      "template_id": 91,
      "hst_score": 0.87,
      "timestamp": "2026-05-25T06:40:00+00:00",
      "message": "OOMKilled in payment-svc",
      "level": "error"
    }
  ]
}
```

The detector fires when any of these signals trips:

| reason         | trigger                                                         |
|----------------|-----------------------------------------------------------------|
| `anomaly_score`| HST score ≥ `HST_THRESHOLD` (default 0.7) for that service       |
| `new_template` | Drain has never seen this template for that service, level ≥ warn |
| `error_burst`  | ≥ 60% errors in the last 60s for that service                    |

## Dynamic credentials

Two ways to provide credentials:

**(a) Env file (.env)** — read once at startup:

```env
LOKI_URL=https://loki.example.com
LOKI_API_KEY=your-bearer-token
```

**(b) Runtime via API** — overrides .env for the running process:

```bash
curl -XPOST http://localhost:9020/credentials \
  -H 'Content-Type: application/json' \
  -d '{
        "source": "loki",
        "credentials": {
          "url": "https://loki.example.com",
          "api_key": "your-bearer-token",
          "query": "{namespace=\"prod\"}"
        }
      }'
```

Check what's currently configured:

```bash
curl http://localhost:9020/credentials
# {"sources": [...], "configured": {"loki": true, "datadog": false, ...}}
```

Clear overrides:

```bash
curl -XDELETE 'http://localhost:9020/credentials?source=loki'
curl -XDELETE  http://localhost:9020/credentials              # all
```

## Supported sources & credential keys

| source       | `credentials` keys                                                                  |
|--------------|-------------------------------------------------------------------------------------|
| `newrelic`   | `api_key` (NRAK-…), `account_id`, `query`                                           |
| `loki`       | `url`, `api_key` (Bearer), `query` (LogQL)                                          |
| `datadog`    | `api_key`, `app_key`, `site`, `query`                                               |
| `cloudwatch` | `access_key_id`, `secret_access_key`, `region`, `log_group`, `log_stream` (opt.)    |
| `sentry`     | `token` (sntrys_…), `org`, `project`                                                |
| `clickhouse` | `url`, `user`, `password`, `database`, `table`, `col_timestamp`, `col_message`, `col_level`, `col_service` |

All connectors return the same row shape:

```python
{"timestamp": <int_ms>, "message": str, "level": str, "service": str}
```

## ML knobs

| env var                  | default | purpose                                              |
|--------------------------|---------|------------------------------------------------------|
| `DRAIN_SIM_TH`           | 0.4     | Drain3 similarity threshold (lower → fewer clusters) |
| `ANOMALY_CONTAMINATION`  | 0.1     | Isolation Forest expected anomaly fraction           |
| `HST_THRESHOLD`          | 0.7     | Half-Space-Trees anomaly cutoff                      |
| `DEFAULT_LOOKBACK_HOURS` | 6       | Used when `window` is omitted                        |
| `MAX_ROWS`               | 100000  | Hard cap per fetch                                   |

## How it works

- **Drain3** (Du & Li, 2017) builds a fixed-depth parse tree over masked log lines, grouping messages that share a structural template. We pre-mask UUIDs, IPs, URLs, paths, dates, durations, hex tokens, floats, and large integers so similar messages collapse into the same template.
- **Isolation Forest** runs per-service over `[log_count, error_rate, warn_rate, unique_services, token_count]` features per template. Templates with unusually short isolation paths are flagged.
- **TF-IDF + PCA** projects templates into 2-D `(x, y)` coordinates clamped to `[5, 95]` — ready for a scatter plot.
- **Half-Space-Trees** (Tan et al., 2011) is an online ensemble that scores each new log against rolling per-service feature distributions, with no retraining cycle.

## License

Apache 2.0 (matches the parent Rocketgraph project).
