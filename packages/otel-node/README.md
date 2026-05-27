# `@rgraph/otel-node`

**An AI agent that auto-instruments any Node.js backend with OpenTelemetry.** It reads your code, understands your framework and dependencies, and writes the right instrumentation file — replacing what is normally a multi-day, error-prone integration with a 90-second command.

Most teams want the Rocketgraph ML engine's clustering and anomaly detection, but lack the upstream pipeline that produces structured telemetry in the first place. `otel-node` closes that gap. Run it once against an Express / Fastify / NestJS / Koa / Hapi / Next.js service and that service starts emitting OTLP traces, metrics, and logs that any OTel-compatible sink — including the ML engine's ClickHouse-backed pipeline — can consume.

---

## What it does

The default mode is **agent mode**: a Claude-powered agent reads your `package.json`, scans the source tree, identifies the framework and HTTP/DB/queue libraries in use, then writes (and merges with) the appropriate instrumentation file. No templates, no guesswork, no manual `@opentelemetry/instrumentation-*` package selection.

A **legacy template mode** (`--legacy`) is also available — deterministic, no LLM, useful for CI environments where every code change must be reproducible.

## Detected frameworks and libraries

| Category | What's detected |
| --- | --- |
| **Frameworks** | Express, Fastify, NestJS, Koa, Hapi, Restify, Next.js, Nuxt |
| **HTTP / RPC** | `http`, `https`, `grpc`, `@grpc/grpc-js` |
| **Databases** | `pg`, `mysql2`, `mongodb`, `mongoose`, `redis`, `ioredis`, `prisma` |
| **Queues** | `amqplib`, `kafkajs`, `aws-sdk`, `@aws-sdk/*` |
| **Package managers** | `npm`, `yarn`, `pnpm`, `bun` (auto-detected from lockfile) |
| **Languages** | TypeScript, JavaScript |

## Setup (90 seconds)

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

## Commands

| Command | What it does |
| --- | --- |
| `otel-node init` | Default. Agent reads the project and writes an OTel instrumentation file. Installs required packages. |
| `otel-node init --legacy` | Template-based generator. No LLM. Deterministic output — ideal for CI. |
| `otel-node init --dry-run --legacy` | Print the file that would be written and the packages that would be installed. No changes. |
| `otel-node instrument` | Agent goes further — adds structured error handlers, span attributes, and observability code throughout the app. |
| `otel-node detect` | JSON report of what the detector sees (framework, libs, package manager, instrumentation path). No changes. |
| `otel-node uninstall` | Remove the generated `instrumentation.ts`/`.js` and its `.bak`. Leaves OTel packages installed. |

### Flags worth knowing

| Flag | Purpose |
| --- | --- |
| `--dir <path>` | Run against a project that is not the current working directory. |
| `--endpoint <url>` | OTLP endpoint URL (legacy mode). Default `http://localhost:4318`. |
| `--service-name <name>` | Override the service name (legacy mode). Defaults to `package.json`'s `name`. |
| `--exporter otlp-http \| otlp-grpc \| console` | Pick the exporter type (legacy mode). |
| `--skip-install` | Write the instrumentation file but don't run the package install (legacy mode). |

## How it fits with the ML engine

```
Your Node service                  OTel Collector              Sink              Rocketgraph ML
─────────────────                  ─────────────              ────              ──────────────
@rgraph/otel-node init       ──>   OTLP HTTP / gRPC    ──>   ClickHouse   ──>   /clusters?source=clickhouse
(writes instrumentation)            (or any platform)        Loki                /anomalies/detect
                                                             Datadog
                                                             New Relic
```

The agent only handles the left half — getting telemetry *out* of your service. The right half is whatever observability platform you already pay for. The ML engine pulls from that sink directly. No custom protocol, no proprietary SDK, no lock-in.

## License

Apache 2.0 (matches the parent Rocketgraph project). See [LICENSE.txt](../../LICENSE.txt).
