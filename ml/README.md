# Rocketgraph ML

Self-hosted log clustering and streaming anomaly detection.

A standalone FastAPI service that points at the monitoring tool you already use (New Relic, Loki, Datadog, CloudWatch, Sentry, ClickHouse), runs **Drain3** template mining + **Isolation Forest** anomaly scoring across the parsed templates, and trains a per-service **Half-Space-Trees** model so you can score new log lines on the fly.

No database. No accounts. Stateless except for the in-memory HST model.

---

## What it does

Rocketgraph turns a high-volume log stream into a small, ranked set of structural patterns and flags the patterns that are statistically unusual. Three things run in sequence:

1. **Drain3** mines log templates. Every line is masked (UUIDs, IPs, paths, numbers collapse to placeholders) and routed through a fixed-depth parse tree. Lines that share a structural template land in the same cluster.
2. **Isolation Forest** scores each template, per service, against a feature vector — log count, error rate, warn rate, unique-service spread, token count. Templates with unusually short isolation paths are flagged.
3. **Half-Space-Trees** is an online ensemble that scores incoming logs in real time. One model per service, no retraining cycle, no manual labels.

The output of a single training pass on **2,002,271 production logs** across nine services (a real burst from a deployment we routinely test against): **58 templates, 9 anomalies, 90 seconds wall-clock on a single container**.

```
2,002,271 raw logs   ──>   58 Drain3 templates   ──>   9 flagged anomalies
9 services                  one feature vector         per-template isolation
                            per template               + per-service HST model
```

Every anomaly carries a `reasons` array — `anomaly_score`, `new_template`, `error_burst` — so downstream alerting can route deterministically. No LLM, no hallucination, no "the model thinks…" — the math is fully explainable and reproducible.

## The tech behind it

| Stage | Algorithm | Why this one |
| --- | --- | --- |
| Template mining | **Drain3** (Du & Li, ICSE 2017) | Fastest known online log parser. O(N) over the stream, no retraining. Handles 100k+ logs/sec on a single core. |
| Per-template scoring | **Isolation Forest** (Liu et al., 2008) | Unsupervised, no labels needed. Linear in N. Works on small feature vectors so it scales to millions of logs. |
| Online detection | **Half-Space-Trees** (Tan et al., IJCAI 2011) | True streaming anomaly detector. Constant memory per service, sub-millisecond scoring per event. Adapts as service behavior shifts. |
| Layout for visualization | **TF-IDF + PCA → MinMax** | Templates project to 2-D `(x, y)` coordinates clamped to `[5, 95]`. Drop them straight into a scatter plot. |

All four are deterministic given the same input. The same window of logs produces the same clusters every time — important when your SRE team needs to compare yesterday's incident to last week's.

## Architecture

```
       ┌───────────────────────────────────────────────────────────┐
       │  Source of record  (Loki / NR / Datadog / CloudWatch /    │
       │                     Sentry / ClickHouse)                  │
       └────────────────────────────┬──────────────────────────────┘
                                    │ pull window (1h | 1d | 7d | absolute)
                                    ▼
                       ┌────────────────────────────┐
                       │  Mask + Drain3             │  → templates
                       └─────────────┬──────────────┘
                                     ▼
        ┌────────────────────────────┴────────────────────────────┐
        │  Isolation Forest (per service)                         │  → cluster JSON
        │  + TF-IDF + PCA 2-D layout                              │
        └────────────────────────────┬────────────────────────────┘
                                     ▼
                       ┌─────────────────────────────┐
                       │  Half-Space-Trees           │  → /anomalies/detect
                       │  (one model per service)    │
                       └─────────────────────────────┘
```

## Quick start

```bash
git clone https://github.com/Rocketgraph/rocketgraph
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

That's the entire install. No agent to deploy, no schema to provision, no upstream account to create.

## Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET`  | `/clusters` | Cluster a window of logs. Returns templates with anomaly scores and 2-D coordinates. |
| `POST` | `/clusters/train` | Same as `/clusters`, plus warms the per-service HST model on the same window. |
| `POST` | `/anomalies/detect` | Score new logs (inline JSON or fetched from a connector) against the trained HST. |
| `POST` | `/credentials` | Set or override connector credentials at runtime. |
| `GET`  | `/credentials` | Inspect which sources are configured. |
| `POST` | `/detector/reset` | Wipe the trained HST. |
| `GET`  | `/health` | Liveness check. |

Time-window flags: `1h`, `6h`, `12h`, `24h`, `1d`, `7d`, `custom` with `&hours=N`, or absolute `start=<ISO>&end=<ISO>` (ClickHouse).

### 1. `GET /clusters` — cluster a window of logs

```bash
curl 'http://localhost:9020/clusters?source=loki&window=1h'
```

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
    { "id": "73", "isAnomaly": true, "isolationDepth": 3, "color": "red", "...": "..." }
  ]
}
```

### 2. `POST /clusters/train` — cluster **and** train the live detector

```bash
curl -XPOST 'http://localhost:9020/clusters/train?source=loki&window=1d'

# Or an absolute window against ClickHouse:
curl -XPOST 'http://localhost:9020/clusters/train?source=clickhouse&start=2026-05-11T09:00:00Z&end=2026-05-11T09:10:00Z'
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

## Connecting to existing observability platforms

Rocketgraph connects to your existing source of record directly — no data duplication, no parallel ingestion pipeline, no agent to install on hosts.

| Source | Auth | Credential keys |
| --- | --- | --- |
| **New Relic** | NerdGraph (NRQL) | `api_key` (NRAK-…), `account_id`, `query` |
| **Grafana Loki** | Bearer token | `url`, `api_key`, `query` (LogQL) |
| **Datadog** | API + App key | `api_key`, `app_key`, `site`, `query` |
| **AWS CloudWatch** | IAM keys | `access_key_id`, `secret_access_key`, `region`, `log_group`, `log_stream` (opt.) |
| **Sentry** | User auth token | `token` (sntrys_…), `org`, `project` |
| **ClickHouse** | HTTP basic auth | `url`, `user`, `password`, `database`, `table`, `col_timestamp`, `col_message`, `col_level`, `col_service` |
| **OpenTelemetry** | OTLP collector | Route into ClickHouse or Loki, then point Rocketgraph at that. See [Routing OpenTelemetry into Rocketgraph](#routing-opentelemetry-into-rocketgraph) below. |

All connectors return the same row shape, so the downstream ML pipeline is identical regardless of source:

```python
{"timestamp": <int_ms>, "message": str, "level": str, "service": str}
```

## Dynamic credentials

Two ways to provide credentials, both first-class:

**(a) Env file (.env)** — read once at startup:

```env
LOKI_URL=https://loki.example.com
LOKI_API_KEY=your-bearer-token
```

**(b) Runtime via API** — overrides .env for the running process, no restart, no config reload:

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

Option (b) is what you want for multi-tenant control planes — pass tenant credentials per request without restarting the service.

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

## Routing OpenTelemetry into Rocketgraph

For platforms that already speak OTel, route logs into ClickHouse and point Rocketgraph at that ClickHouse. A minimal collector config:

```yaml
receivers:
  otlp:
    protocols:
      grpc: { endpoint: 0.0.0.0:4317 }
      http: { endpoint: 0.0.0.0:4318 }

exporters:
  clickhouse:
    endpoint: tcp://clickhouse:9000
    database: otel
    logs_table_name: otel_logs

service:
  pipelines:
    logs:
      receivers: [otlp]
      exporters: [clickhouse]
```

Then in Rocketgraph's `.env`:

```env
CH_URL=http://clickhouse:8123
CH_DATABASE=otel
CH_TABLE=otel_logs
```

Done. No further integration code.

## Tuning

| Env var                  | Default | Purpose                                              |
|--------------------------|---------|------------------------------------------------------|
| `DRAIN_SIM_TH`           | `0.4`   | Drain3 similarity threshold (lower → fewer, broader templates) |
| `ANOMALY_CONTAMINATION`  | `0.1`   | Isolation Forest expected anomaly fraction           |
| `HST_THRESHOLD`          | `0.7`   | Half-Space-Trees anomaly cutoff                      |
| `DEFAULT_LOOKBACK_HOURS` | `6`     | Used when `window` is omitted                        |
| `MAX_ROWS`               | `100000`| Hard cap per fetch — bump for high-volume training windows |

## How it works (under the hood)

- **Drain3** (Du & Li, 2017) builds a fixed-depth parse tree over masked log lines, grouping messages that share a structural template. We pre-mask UUIDs, IPs, URLs, paths, dates, durations, hex tokens, floats, and large integers so similar messages collapse into the same template.
- **Isolation Forest** runs per-service over `[log_count, error_rate, warn_rate, unique_services, token_count]` features per template. Templates with unusually short isolation paths are flagged.
- **TF-IDF + PCA** projects templates into 2-D `(x, y)` coordinates clamped to `[5, 95]` — ready for a scatter plot.
- **Half-Space-Trees** (Tan et al., 2011) is an online ensemble that scores each new log against rolling per-service feature distributions, with no retraining cycle.

## Performance

Measured on a single container, 4 vCPU, 8 GB RAM, against a real production-shaped workload:

| Workload | Result |
| --- | --- |
| Drain3 template mining | ~25,000 logs/sec/core |
| Isolation Forest scoring | <50 ms per service for ≤500 templates |
| Half-Space-Trees scoring | sub-millisecond per event |
| End-to-end on 2M logs / 9 services | ~90 seconds (bottlenecked on HTTP fetch from ClickHouse, not ML) |

The ML pipeline scales linearly with log volume. Memory footprint is bounded by the number of templates, not the number of logs.

## Deployment

Rocketgraph is designed to run inside your network.

- **Single container.** `docker compose up` brings up the ML engine. That is the only required process.
- **Kubernetes.** The ML engine is a single stateless deployment behind an internal LB. The `Dockerfile` in this directory is the build artifact you'd deploy.
- **VPC-only.** No outbound traffic required. Connectors only call the observability platforms you've configured. The container does not phone home.
- **Secrets.** Use `.env` for static credentials, `POST /credentials` for dynamic or per-tenant credentials. Both can be combined.

## Security

- Apache 2.0 licensed. Source is auditable.
- No telemetry, no analytics, no outbound calls beyond the connectors you configure.
- Credentials are held in memory only — never persisted to disk or logs.
- Optional signing-secret middleware (`SIGNING_SECRET` env) gates every endpoint behind an `X-Signing-Secret` header.
- Designed for FedRAMP / HIPAA / SOC2-style environments. Can be deployed in air-gapped networks.

## Compatibility

| Platform | Status |
| --- | --- |
| OpenTelemetry (logs) | Supported |
| Grafana Loki | Supported |
| New Relic | Supported |
| Datadog | Supported |
| AWS CloudWatch Logs | Supported |
| Sentry | Supported |
| ClickHouse | Supported |
| Splunk | Roadmap |
| Elastic / OpenSearch | Roadmap |
| Azure Monitor | Roadmap |
| GCP Cloud Logging | Roadmap |

## License

Apache 2.0 (matches the parent Rocketgraph project). See [LICENSE.txt](../LICENSE.txt).
