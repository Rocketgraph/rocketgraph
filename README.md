<p align="center">
  <a href="https://rocketgraph.app">
    <img alt="Rocketgraph" src="./images/rocketgraph-logo-dark.png" width="320">
  </a>
</p>

<h1 align="center">Rocketgraph</h1>

<p align="center">
  <strong>Self-hosted log clustering and streaming anomaly detection that drops in next to the observability stack you already run.</strong>
</p>

<p align="center">
  <a href="#the-ml-engine">ML Engine</a>
  &nbsp;&bull;&nbsp;
  <a href="#the-otel-node-cli">OTel Agent CLI</a>
  &nbsp;&bull;&nbsp;
  <a href="#example-setups">Examples</a>
  &nbsp;&bull;&nbsp;
  <a href="https://rocketgraph.app">Website</a>
  &nbsp;&bull;&nbsp;
  <a href="https://discord.gg/YHVnZ5WT">Community</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache_2.0-blue" alt="Apache 2.0">
  <img src="https://img.shields.io/badge/python-3.11+-brightgreen" alt="Python">
  <img src="https://img.shields.io/badge/docker-ready-2496ed?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/OpenTelemetry-supported-blue?logo=opentelemetry" alt="OpenTelemetry">
</p>

---

## Why Rocketgraph

Most organizations already pay for log storage — Datadog, New Relic, Loki, CloudWatch, Sentry, or a self-hosted ClickHouse. None of these systems tell you *what is unusual right now*. They tell you what you searched for.

Rocketgraph sits next to those systems, pulls a window of their logs, mines structural templates, and reports the anomalous ones. It runs entirely inside your network. Your logs do not leave your VPC. There is no SaaS ingestion tier to pay for.

Two components ship in this repository:

| Component | Purpose |
| --- | --- |
| **ML engine** ([`ml/`](./ml)) | A self-hosted FastAPI service that clusters logs with Drain3 + Isolation Forest and detects live anomalies with Half-Space-Trees. Six built-in connectors. |
| **`@rgraph/otel-node` CLI** ([`packages/otel-node/`](./packages/otel-node)) | An AI agent that reads any Node.js codebase and writes the OpenTelemetry instrumentation for it — so the telemetry the ML engine consumes can be produced in 90 seconds, not a 3-day integration. |

---

## The ML Engine

<p align="center">
  <img src="./images/show-clusters.gif" alt="Rocketgraph ML — 2M logs clustered into 58 templates in 90 seconds" width="820">
</p>

### What it does

Rocketgraph turns a high-volume log stream into a small, ranked set of structural patterns and flags the patterns that are statistically unusual. It runs three things in sequence:

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

### The tech behind it

| Stage | Algorithm | Why this one |
| --- | --- | --- |
| Template mining | **Drain3** (Du & Li, ICSE 2017) | Fastest known online log parser. O(N) over the stream, no retraining. Handles 100k+ logs/sec on a single core. |
| Per-template scoring | **Isolation Forest** (Liu et al., 2008) | Unsupervised, no labels needed. Linear in N. Works on small feature vectors so it scales to millions of logs. |
| Online detection | **Half-Space-Trees** (Tan et al., IJCAI 2011) | True streaming anomaly detector. Constant memory per service, sub-millisecond scoring per event. Adapts as service behavior shifts. |
| Layout for visualization | **TF-IDF + PCA → MinMax** | Templates project to 2-D `(x, y)` coordinates clamped to `[5, 95]`. Drop them straight into a scatter plot. |

All four are deterministic given the same input. The same window of logs produces the same clusters every time — important when your SRE team needs to compare yesterday's incident to last week's.

### Architecture

```
       ┌───────────────────────────────────────────────────────────┐
       │  Source of record  (Loki / NR / Datadog / CloudWatch /    │
       │                     Sentry / ClickHouse)                  │
       └────────────────────────────┬──────────────────────────────┘
                                    │ pull window (1h | 1d | 7d | absolute)
                                    ▼
                       ┌────────────────────────────┐
                       │  Mask + Drain3              │  → templates
                       └─────────────┬───────────────┘
                                     ▼
        ┌────────────────────────────┴────────────────────────────┐
        │  Isolation Forest (per service)                          │  → cluster JSON
        │  + TF-IDF + PCA 2-D layout                               │
        └────────────────────────────┬─────────────────────────────┘
                                     ▼
                       ┌─────────────────────────────┐
                       │  Half-Space-Trees           │  → /anomalies/detect
                       │  (one model per service)    │
                       └─────────────────────────────┘
```

### Setup (90 seconds)

```bash
git clone https://github.com/rocketgraph/rocketgraph
cd rocketgraph/ml
cp .env.example .env             # fill in whichever sources you have
docker compose up --build        # → http://localhost:9020
```

Or without Docker:

```bash
pip install -r requirements.txt
uvicorn app:app --port 9020
```

That is the entire install. There is no agent to deploy, no schema to provision, no upstream account to create.

<p align="center">
  <em>📽️ Screencast slot: from <code>docker compose up</code> to first <code>/clusters</code> response.</em><br>
  <em>Suggested file: <code>images/ml-engine-setup.gif</code></em>
</p>

### Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/clusters` | Cluster a window of logs. Returns templates with anomaly scores and 2-D coordinates. |
| `POST` | `/clusters/train` | Same as `/clusters`, plus warms the per-service HST model on the same window. |
| `POST` | `/anomalies/detect` | Score new logs (inline JSON or fetched from a connector) against the trained HST. |
| `POST` | `/credentials` | Set or override connector credentials at runtime. |
| `GET` | `/credentials` | Inspect which sources are configured. |
| `POST` | `/detector/reset` | Wipe the trained HST. |
| `GET` | `/health` | Liveness check. |

Time-window flags: `1h`, `6h`, `12h`, `24h`, `1d`, `7d`, `custom` with `&hours=N`, or absolute `start=<ISO>&end=<ISO>` (ClickHouse).

```bash
# Cluster the last hour of Loki logs
curl 'http://localhost:9020/clusters?source=loki&window=1h'

# Train the streaming detector on a 6-minute deployment burst
curl -XPOST 'http://localhost:9020/clusters/train?source=clickhouse&start=2026-05-11T09:00:00Z&end=2026-05-11T09:10:00Z'

# Score five live log lines against the trained model
curl -XPOST http://localhost:9020/anomalies/detect \
  -H 'Content-Type: application/json' \
  -d '{"logs":[{"timestamp":1716624000000,"message":"DB pool exhausted","level":"error","service":"db-service"}]}'
```

### Connecting to existing observability platforms

Rocketgraph connects to your existing source of record directly — there is no data duplication, no parallel ingestion pipeline, no agent to install on hosts.

| Source | Auth | Credential keys |
| --- | --- | --- |
| **New Relic** | NerdGraph (NRQL) | `api_key` (NRAK-…), `account_id` |
| **Grafana Loki** | Bearer token | `url`, `api_key`, `query` (LogQL) |
| **Datadog** | API + App key | `api_key`, `app_key`, `site`, `query` |
| **AWS CloudWatch** | IAM keys | `access_key_id`, `secret_access_key`, `region`, `log_group` |
| **Sentry** | User auth token | `token`, `org`, `project` |
| **ClickHouse** | HTTP basic auth | `url`, `user`, `password`, `database`, `table`, column overrides |
| **OpenTelemetry** | OTLP collector | Route into ClickHouse or Loki, then point Rocketgraph at that. See [`example-setups/otel-collector/`](./example-setups). |

Every connector returns the same row shape, so the downstream ML pipeline is identical regardless of source:

```python
{"timestamp": <int_ms>, "message": str, "level": str, "service": str}
```

### Dynamic credentials

Two ways to provide credentials, both first-class:

```bash
# Option A — .env file, read at startup
LOKI_URL=https://loki.example.com
LOKI_API_KEY=glsa_...
```

```bash
# Option B — at runtime, no restart, no config reload
curl -XPOST http://localhost:9020/credentials \
  -H 'Content-Type: application/json' \
  -d '{"source":"loki","credentials":{"url":"https://loki.example.com","api_key":"glsa_..."}}'
```

Option B is what you want for multi-tenant control planes — pass tenant credentials per request without restarting the service.

### Tuning

| Env var | Default | Purpose |
| --- | --- | --- |
| `DRAIN_SIM_TH` | `0.4` | Drain3 similarity threshold. Lower → fewer, broader templates. |
| `ANOMALY_CONTAMINATION` | `0.1` | Expected fraction of anomalies per service. |
| `HST_THRESHOLD` | `0.7` | Half-Space-Trees flag cutoff. |
| `DEFAULT_LOOKBACK_HOURS` | `6` | Window used when none specified. |
| `MAX_ROWS` | `100000` | Hard cap per fetch. Bump for high-volume training windows. |

---

## The `otel-node` CLI

<p align="center">
  <em>📽️ Screencast slot: <code>npx @rgraph/otel-node init</code> → agent reads the project → instrumentation written → traces flowing.</em><br>
  <em>Suggested file: <code>images/otel-node-init.gif</code></em>
</p>

`@rgraph/otel-node` is an **AI agent that auto-instruments any Node.js backend with OpenTelemetry**. It reads your code, understands your framework and dependencies, and writes the right instrumentation file — replacing what is normally a multi-day, error-prone integration with a 90-second command.

Most teams want the ML engine's clustering and anomaly detection, but lack the upstream pipeline that produces structured telemetry in the first place. `otel-node` closes that gap. Run it once against an Express/Fastify/NestJS/Koa/Hapi/Next.js service and that service starts emitting OTLP traces, metrics, and logs that any OTel-compatible sink — including the ML engine's ClickHouse-backed pipeline — can consume.

### What it does

The default mode is **agent mode**: a Claude-powered agent reads your `package.json`, scans the source tree, identifies the framework and HTTP/DB/queue libraries in use, then writes (and merges with) the appropriate instrumentation file. No templates, no guesswork, no manual `@opentelemetry/instrumentation-*` package selection.

A **legacy template mode** (`--legacy`) is also available — deterministic, no LLM, useful for CI environments where every code change must be reproducible.

### Detected frameworks and libraries

| Frameworks | Express, Fastify, NestJS, Koa, Hapi, Restify, Next.js, Nuxt |
| --- | --- |
| **HTTP / RPC** | `http`, `https`, `grpc`, `@grpc/grpc-js` |
| **Databases** | `pg`, `mysql2`, `mongodb`, `mongoose`, `redis`, `ioredis`, `prisma` |
| **Queues** | `amqplib`, `kafkajs`, `aws-sdk`, `@aws-sdk/*` |
| **Package managers** | `npm`, `yarn`, `pnpm`, `bun` (auto-detected from lockfile) |
| **Languages** | TypeScript, JavaScript |

### Setup (90 seconds)

```bash
# Install nothing — run it directly:
export ROCKETGRAPH_API_KEY=rg_live_xxxxxxxxxxxx
cd ~/your-node-service
npx @rgraph/otel-node init
```

That's it. The agent will:

1. Detect your framework, language, and package manager from your `package.json` and lockfile.
2. Scan dependencies to find every HTTP, database, queue, and cache client in use.
3. Write an `instrumentation.ts` (or `.js`) file tailored to your codebase. If one exists, it backs it up to `.bak` and merges.
4. Install the necessary `@opentelemetry/*` packages with the right package manager.
5. Print the exact start-command flag (`--require` or `--import`) to wire the file in.

For Next.js, it picks up `experimental.instrumentationHook` automatically. For TypeScript projects, it emits a TS file with the right `tsx` / `ts-node` start hints.

### Commands

| Command | What it does |
| --- | --- |
| `otel-node init` | Default. Agent reads the project and writes an OTel instrumentation file. Installs required packages. |
| `otel-node init --legacy` | Template-based generator. No LLM. Deterministic output — ideal for CI. |
| `otel-node init --dry-run --legacy` | Print the file that would be written and the packages that would be installed. No changes. |
| `otel-node instrument` | Agent goes further — adds structured error handlers, span attributes, and observability code throughout the app. |
| `otel-node detect` | JSON report of what the detector sees (framework, libs, package manager, instrumentation path). No changes. |
| `otel-node uninstall` | Remove the generated `instrumentation.ts`/`.js` and its `.bak`. Leaves OTel packages installed. |

Flags worth knowing:

| Flag | Purpose |
| --- | --- |
| `--dir <path>` | Run against a project that is not the current working directory. |
| `--endpoint <url>` | OTLP endpoint URL (legacy mode). Default `http://localhost:4318`. |
| `--service-name <name>` | Override the service name (legacy mode). Defaults to `package.json`'s `name`. |
| `--exporter otlp-http | otlp-grpc | console` | Pick the exporter type (legacy mode). |
| `--skip-install` | Write the instrumentation file but don't run the package install (legacy mode). |

### How it fits with the ML engine

```
Your Node service                  OTel Collector              Sink              Rocketgraph ML
─────────────────                  ─────────────              ────              ──────────────
@rgraph/otel-node init       ──>   OTLP HTTP / gRPC    ──>   ClickHouse   ──>   /clusters?source=clickhouse
(writes instrumentation)            (or any platform)        Loki                /anomalies/detect
                                                             Datadog
                                                             New Relic
```

The agent only handles the left half — getting telemetry *out* of your service. The right half is whatever observability platform you already pay for. The ML engine pulls from that sink directly. No custom protocol, no proprietary SDK, no lock-in.

---

## Example Setups

The [`example-setups/`](./example-setups) directory contains end-to-end reference deployments — small front-end applications that talk to a Rocketgraph backend through GraphQL. Useful for confirming auth, datasource registration, and the API contract before you wire production traffic in.

| Example | Stack | What it demonstrates |
| --- | --- | --- |
| [`auth/`](./example-setups/auth) | React + Cypress | Email/password sign-in flow against the bootstrapped auth tables. |
| [`social/`](./example-setups/social) | React + Cypress | Social-graph schema with friend connections. |
| [`social-google/`](./example-setups/social-google) | React + Google OAuth | Social login via Google SSO. |
| [`todos/`](./example-setups/todos) | React + Cypress | Multi-tenant CRUD against Hasura. |
| [`movie-voting/`](./example-setups/movie-voting) | React + Cypress | Real-time subscriptions over the GraphQL endpoint. |
| [`bookstore-app/`](./example-setups/bookstore-app) | React + Cypress | E-commerce schema with order lifecycle. |

Each example ships with a `README.md` and a Cypress suite, so you can verify the integration end-to-end before adopting it.

### OpenTelemetry collectors

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

---

## Performance

Measured on a single container, 4 vCPU, 8 GB RAM, against a real production-shaped workload:

| Workload | Result |
| --- | --- |
| Drain3 template mining | ~25,000 logs/sec/core |
| Isolation Forest scoring | <50 ms per service for ≤500 templates |
| Half-Space-Trees scoring | sub-millisecond per event |
| End-to-end on 2M logs / 9 services | ~90 seconds (bottlenecked on HTTP fetch from ClickHouse, not ML) |

The ML pipeline scales linearly with log volume. Memory footprint is bounded by the number of templates, not the number of logs.

---

## Deployment

Rocketgraph is designed to run inside your network.

- **Single container.** `docker compose up` brings up the ML engine. That is the only required process.
- **Kubernetes.** Helm charts live in [`example-setups/`](./example-setups). The ML engine is a single stateless deployment behind an internal LB.
- **VPC-only.** No outbound traffic required. Connectors only call the observability platforms you've configured. The container does not phone home.
- **Secrets.** Use `.env` for static credentials, `POST /credentials` for dynamic or per-tenant credentials. Both can be combined.

---

## Security

- Apache 2.0 licensed. Source is auditable.
- No telemetry, no analytics, no outbound calls beyond the connectors you configure.
- Credentials are held in memory only — never persisted to disk or logs.
- Optional signing-secret middleware (`SIGNING_SECRET` env) gates every endpoint behind an `X-Signing-Secret` header.
- Designed for FedRAMP / HIPAA / SOC2-style environments. The control plane and ML engine can be deployed in air-gapped networks.

---

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

---

## Community

- [Discord](https://discord.gg/YHVnZ5WT) for support and design discussions
- [GitHub Issues](https://github.com/rocketgraph) for bug reports and feature requests
- [Twitter / X](https://twitter.com/RGraphql) for release notes

---

## Contributing

Pull requests welcome. The most impactful contributions right now:

- New ML-engine connectors (Splunk, OpenSearch, Azure Monitor, GCP Cloud Logging).
- Additional framework support in `@rgraph/otel-node` (NestJS variants, Remix, Bun-native services).
- Additional OTel collector reference configurations under `example-setups/`.
- Tuning notes for high-cardinality services.

---

## License

Apache 2.0. See [LICENSE](LICENSE.txt) for the full text.

---

<p align="center">
  <strong>Self-hosted. Open source. Drops in next to what you already run.</strong>
  <br>
  <a href="https://rocketgraph.app">rocketgraph.app</a>
</p>
