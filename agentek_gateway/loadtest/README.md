# Load tests of the subscription pool

Scripts that reproduce the gateway behaviour under load: dead subscriptions, a flushed Redis, Redis traffic per request,
a stop under load. They run against throw-away containers (own network, Redis, Postgres, a scripted Codex backend and a
gateway built from this checkout) and never touch another environment.

## Run

```bash
export LT_IMAGE=<gateway image built from this fork>
loadtest/rig.sh up
loadtest/rig.sh gateway lt-gw-a 4101            # plugin from this checkout; pass another plugin dir as the 3rd argument to compare
python3 loadtest/experiments.py setup 12
python3 loadtest/experiments.py dead 10 40 300  # 10 of 12 dead, concurrency 40, 300 requests
loadtest/rig.sh down
```

Run the load under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0`; the rig takes about 4 GB with one gateway.
The rig's Redis password is `a/b@c:d#e`, so every start also checks that the endpoint reaches the plugin and LiteLLM through separate variables.

## Experiments

`dead` prints client statuses (leave 31 s between runs: the process keeps the states it wrote for 30 s, and a reset in Redis does not clear them), p99, throughput and how many requests reached the dead accounts. `cold` repeats the first request
after a reset; trials are 31 s apart because a process remembers the states it wrote for 30 s. `flush` empties the plugin database
in the middle of the load. `calls` prints Redis commands per request for the chat or Responses path. `usage` shows which
subscriptions have limit windows after chat requests. `stop` runs `docker stop -t 30` under load with dead accounts and prints
the time and the exit code; 137 means the process was killed.

## Reference numbers

One gateway process, one core, concurrency 40, 10 of 12 subscriptions dead, 300 requests. Absolute throughput depends on the host;
compare runs made side by side.

| | plugin of the acceptance build | with the fixes |
|---|---|---|
| client 429 | 37 / 11 / 0 | 0 / 0 / 0 |
| requests that reached dead accounts | 1256 / 1099 / 712 | 48 / 60 / 160 |
| `docker stop -t 30` under load | 30.7 s, 31.2 s (137), 32.4 s (137) | 11.6 s, 7.1 s, 8.5 s (all 0) |
