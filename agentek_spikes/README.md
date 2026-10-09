# Subscription-pool spikes (part 0)

Throwaway experiments for `openspec/changes/llm-subscription-pool` (console repo), tasks 0.1-0.12.
This branch is never merged; the findings are recorded in windbit/issues#1599 and in that change's `design.md`.

## Setup

- Base image: CI image of the fork head, `ghcr.io/windbit/agentek-litellm:b649adb72d24cce5b6fd0e06f22fdf9a34e05ac1`.
- `Dockerfile.spike` adds the plugin package with the same `COPY` line the real `Dockerfile` gets (task 0.5).
  Day-to-day iteration bind-mounts `agentek_gateway/` instead of rebuilding (`DEV_MOUNT=1 ./up.sh`).
- Throwaway Postgres and Redis containers on the `poolspike` docker network; `mock_upstream.py` plays the ChatGPT Codex backend
  (`/responses`, behaviour per account id, scripted: `usage_limit`, `sse_failed`, `midstream_error`, `model_not_supported`, ...).
  Response shapes follow sub2api's parser (429 body `error.type=usage_limit_reached`, `resets_at`, `x-codex-*` headers).
- No real subscription was used; everything below runs against the mock.

```bash
docker network create poolspike
docker run -d --name poolspike-pg --network poolspike -e POSTGRES_PASSWORD=spike -e POSTGRES_DB=litellm -p 127.0.0.1:55432:5432 postgres:16-alpine
docker run -d --name poolspike-redis --network poolspike -p 127.0.0.1:56379:6379 redis:7-alpine
docker build -f agentek_spikes/Dockerfile.spike -t agentek-litellm:spike .   # from services/litellm
DEV_MOUNT=1 agentek_spikes/up.sh gw1 54000   # reads spike-config.yaml; ctl.json is created at run time                                   # gateway on :54000, mock on :59000
python3 agentek_spikes/s01_setup.py && python3 agentek_spikes/s03_solo_setup.py
```

## Map

| Task | Script | Log |
|------|--------|-----|
| 0.1 request_kwargs, session id | `s02_filter_kwargs.py`, `s02b_retry_view.py`, inline in `logs/0.1_session_id.txt` | `logs/spike_events.jsonl` (MARK lines label runs) |
| 0.2 429 / response.failed / stream success | `s04_spike02.py` | `logs/0.2_run1.txt` |
| 0.3 invisible errors | `s05_spike03.py` | `logs/0.3_run1.txt`, `logs/0.3_run2_stream_hook.txt` |
| 0.4 model not supported | `s06_spike04.py` | `logs/0.4_run2.txt` |
| 0.5 package, hook, router, metrics | `agentek_gateway/spike.py`, `idempotency_probe.py` | `logs/0.5_idempotency.txt` |
| 0.6 selection latency | `bench_select.py` (open-loop latency from the planned arrival time), `bench_profile.py`, `bench_stalls.py` | `logs/0.6_*` (`run4` is the planned-arrival run) |
| 0.7 prisma, migrations, rollback | `prisma/build.sh`, `prisma/run_0.7.sh`, `prisma/run_0.7b.sh` | `logs/0.7_run.txt`, `logs/0.7b_run.txt` |
| 0.8 tag rewrite | `s07_spike08_10.py` | `logs/0.8_run.txt` |
| 0.9 retries | `retry_policy_probe.py` | `logs/0.9_run1.txt`, `logs/0.9_gateway_check.txt` |
| 0.10 filter + failover | `s08_spike10.py`, `s09_spike10b.py`, `s10_spike10c.py`, `s11_spike10d.py` | `logs/0.10_run1..4.txt` |
| 0.12 egress / correlated errors | read-only SQL aggregates in `sql/`, `analysis_0.12.py` (two null models) | `logs/0.12_*` |
| Review follow-up R1-R4 | `s12_review_spikes.py` (in-process `Router`), `s13_review_gateway.py` (gateway) | `logs/review_*`, `logs/0.5_gateway_checks.txt` |
