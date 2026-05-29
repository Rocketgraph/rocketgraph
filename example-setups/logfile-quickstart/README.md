# Log-file quickstart — run anomaly detection on a downloaded `.log` file

You already have a logging tool (Datadog, CloudWatch, Loki, …). You don't want to
wire up API keys just to *see if this is worth it*. So: **download a log file,
drop it here, run one command.** No database, no agent, no account, nothing
leaves your machine.

This is the fastest way to try Rocketgraph ML — and it's the same engine and the
same endpoints you'd later point at a live source.

---

## TL;DR

```bash
cd example-setups/logfile-quickstart

# Use your own export…
cp ~/Downloads/your-logs.log ./logs/app.log
#   …or generate a realistic sample with an incident baked in:
python gen_sample_log.py

docker compose up --build -d      # ML engine on http://localhost:9020
bash run.sh                       # cluster + detect, pretty-printed
```

That's it. `run.sh` clusters the whole file into a handful of templates and then
scores brand-new lines against the trained model.

---

## Getting a log file out of Datadog

Any of these work — the connector auto-detects the format:

| How you exported | File looks like | `FILE_FORMAT` |
| --- | --- | --- |
| **Logs UI → Export → CSV** | `Date,Service,Status,Message` rows | `auto` (or `csv`) |
| **Logs API / Log Forwarder** | one JSON object per line (NDJSON), fields under `attributes` | `auto` (or `json`) |
| **Raw app log / `kubectl logs > app.log`** | plain text lines | `auto` (or `text`) |

Drop the file at `./logs/app.log` (or change `FILE_PATH` in `docker-compose.yml`
to match your filename). The whole file is treated as the analysis window — you
don't pass a time range, because a downloaded snapshot is already the slice you
care about.

> No Datadog handy? Run `python gen_sample_log.py` to write a 15k-line sample
> across three services with a 2-minute incident hiding at the end (an OOM burst
> and a brand-new "database failover" template the model has never seen).
> Use `--format csv` or `--format text` to try the other shapes.

---

## What you'll see

**Step 1 — cluster the file and train the detector**

```bash
curl -X POST 'http://localhost:9020/clusters/train?source=file' | jq
```

~15,000 raw lines collapse to ~11 structural templates. The brand-new failover
template — 8 lines, never seen before, error level — comes back flagged as an
anomaly cluster. No rules written, no labels.

**Step 2 — score new lines as they arrive**

```bash
curl -X POST 'http://localhost:9020/anomalies/detect' \
  -H 'Content-Type: application/json' \
  -d '{"logs":[{"timestamp":1716624000000,"service":"payment-svc","level":"error",
        "message":"TLS handshake failed with upstream vault-proxy after 3 retries"}]}'
```

A never-before-seen error returns with `reasons: ["anomaly_score","new_template"]`
and an HST score around `0.9`. A routine login line is dropped. That `reasons`
array is what you route on — `new_template` → Jira, `error_burst` → page oncall.

---

## Pointing at your own file

Edit `docker-compose.yml`:

```yaml
volumes:
  - ./logs:/data:ro
environment:
  FILE_PATH:    /data/app.log     # the file inside the container
  FILE_FORMAT:  auto              # auto | json | csv | text
  FILE_SERVICE: payment-svc       # optional fallback when a line has no service
```

Or set it at runtime without touching compose:

```bash
curl -X POST http://localhost:9020/credentials \
  -H 'Content-Type: application/json' \
  -d '{"source":"file","credentials":{"path":"/data/other.log","format":"text"}}'
```

---

## Tuning

| Env var | Default | Effect |
| --- | --- | --- |
| `DRAIN_SIM_TH` | `0.4` | lower → fewer, broader templates |
| `ANOMALY_CONTAMINATION` | `0.1` | expected fraction of anomalous templates |
| `HST_THRESHOLD` | `0.7` | streaming score cutoff (raise to 0.8 for fewer alerts) |

---

## Next step: point it at the live source

When the file run convinces you, swap `source=file` for `source=datadog` (or
loki, cloudwatch, sentry, clickhouse, newrelic) and give it credentials — the
endpoints are identical. See [`../../ml/README.md`](../../ml/README.md).
