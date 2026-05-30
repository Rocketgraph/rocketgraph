# Anomaly detection quickstart

A self-contained demo that shows the Rocketgraph ML pipeline in action against synthetic but realistic logs.

It spins up:

1. A local **ClickHouse** that holds the log stream.
2. The **Rocketgraph ML** engine pointed at that ClickHouse.

Then a seed script writes ~15k normal log lines plus an injected incident (a burst of `OOMKilled` errors and a brand-new `Database failover` template) so you can see the detector light up.

## Run it

```bash
cd example-setups/anomaly-detection-quickstart
docker compose up --build       # ClickHouse + Rocketgraph ML
python seed_logs.py             # load synthetic logs into ClickHouse
bash demo.sh                    # train, then score new logs
```

## What you'll see

- `/clusters/train` returns ~10 to 15 Drain3 templates with one or two of them flagged as anomalies (the OOM burst and the failover template).
- The inline `/anomalies/detect` call flags the OOM line as `anomaly_score` + `new_template`, ignores the boring login line.
- The connector-fed `/anomalies/detect` reaches into ClickHouse, pulls the last hour, and returns only the rows the trained HST flagged.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Brings up ClickHouse + the ML engine, wired together. |
| `init-clickhouse.sql` | Creates `demo.logs` on first boot. |
| `seed_logs.py` | Generates synthetic + injected-incident logs and INSERTs them. |
| `demo.sh` | End-to-end calls against the ML API. |
