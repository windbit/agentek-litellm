"""0.6: deployment selection latency at 600 / 1800 deployments with Redis cooldown cache and a filter holding a policy for 500 keys.

Runs inside the spike image (same litellm/redis as the gateway):
  docker run --rm --memory 1500m --network poolspike -v $PWD:/spikes --entrypoint python agentek-litellm:spike /spikes/bench_select.py
"""
import asyncio, json, os, random, statistics, sys, time

import litellm
from litellm import Router
from litellm.integrations.custom_logger import CustomLogger
import redis.asyncio as aioredis

REDIS_HOST, REDIS_PORT = "poolspike-redis", 6379
MODELS_PER_SUB = 20
KEY_COUNT = 500
CALLS = int(os.environ.get("CALLS", "3000"))
WARMUP = 200


class SnapshotFilter(CustomLogger):
    """Planned shape: subscription state lives in a process-local snapshot refreshed once per second (not one Redis MGET per selection)."""

    def __init__(self, redis_client, sub_names, policy):
        super().__init__()
        self.redis = redis_client
        self.sub_names = sub_names
        self.policy = policy
        self.state = {}
        self.window = {}

    async def refresh_forever(self):
        while True:
            keys = [f"agentek:sub:{n}:state" for n in self.sub_names] + [f"agentek:sub:{n}:win" for n in self.sub_names]
            vals = await self.redis.mget(keys)
            half = len(self.sub_names)
            self.state = {n: vals[i] for i, n in enumerate(self.sub_names)}
            self.window = {n: int(vals[half + i] or 0) for i, n in enumerate(self.sub_names)}
            await asyncio.sleep(1.0)

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):
        md = (request_kwargs or {}).get("litellm_metadata") or {}
        labels = (md.get("user_api_key_metadata") or {}).get("labels") or []
        allowed = self.policy.get(labels[0]) if labels else None
        best = None
        for dep in healthy_deployments:
            cred = dep["litellm_params"].get("litellm_credential_name")
            if allowed is not None and cred not in allowed:
                continue
            rank = (self.state.get(cred) is not None, self.window.get(cred, 0), cred)
            if best is None or rank < best[0]:
                best = (rank, dep)
        if best is None:
            raise RuntimeError("no candidates")
        return [best[1]]


class PolicyFilter(CustomLogger):
    """Same shape of work as the planned plugin filter: policy lookup by key labels (500 keys), one Redis MGET of
    subscription state for the group, ordering, return ONE deployment."""

    def __init__(self, redis_client, sub_names, policy):
        super().__init__()
        self.redis = redis_client
        self.sub_names = sub_names
        self.policy = policy

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):
        md = (request_kwargs or {}).get("litellm_metadata") or {}
        labels = (md.get("user_api_key_metadata") or {}).get("labels") or []
        allowed = self.policy.get(labels[0]) if labels else None
        cands = []
        for dep in healthy_deployments:
            cred = dep["litellm_params"].get("litellm_credential_name")
            if allowed is not None and cred not in allowed:
                continue
            cands.append((cred, dep))
        if not cands:
            raise RuntimeError("no candidates")
        keys = [f"agentek:sub:{cred}:state" for cred, _ in cands] + [f"agentek:sub:{cred}:win" for cred, _ in cands]
        vals = await self.redis.mget(keys)
        half = len(cands)
        ranked = sorted(range(half), key=lambda i: (vals[i] is not None, int(vals[half + i] or 0), cands[i][0]))
        return [cands[ranked[0]][1]]


def build_model_list(subs):
    out = []
    for sub in range(subs):
        for model in range(MODELS_PER_SUB):
            out.append({
                "model_name": f"m{model}",
                "litellm_params": {"model": "chatgpt/gpt-5.4", "litellm_credential_name": f"sub-{sub}",
                                   "chatgpt_auth": {"access_token": "x", "expires_at": 4102444800, "account_id": f"a{sub}"}},
                "model_info": {"id": f"sub:sub-{sub}:m{model}", "mode": "responses"},
            })
    return out


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))]


async def measure(router, concurrency, key_labels):
    lat = []

    async def one(idx):
        kwargs = {"litellm_metadata": {"user_api_key_metadata": {"labels": [key_labels[idx % len(key_labels)]]}}, "input": "x"}
        t0 = time.perf_counter()
        dep = await router.async_get_available_deployment(model=f"m{idx % MODELS_PER_SUB}", request_kwargs=kwargs, input="x")
        lat.append((time.perf_counter() - t0) * 1000)
        return dep

    for batch_start in range(0, WARMUP, concurrency):
        await asyncio.gather(*[one(i) for i in range(batch_start, batch_start + concurrency)])
    lat.clear()
    t_all = time.perf_counter()
    for batch_start in range(0, CALLS, concurrency):
        await asyncio.gather(*[one(i) for i in range(batch_start, batch_start + concurrency)])
    wall = time.perf_counter() - t_all
    return {"p50_ms": round(statistics.median(lat), 2), "p95_ms": round(pct(lat, 0.95), 2), "p99_ms": round(pct(lat, 0.99), 2),
            "max_ms": round(max(lat), 2), "calls": len(lat), "rps": round(len(lat) / wall)}


async def measure_open_loop(router, rate, key_labels, seconds=10):
    """Arrivals at a fixed rate (not a closed loop). Latency is measured from the PLANNED arrival time, so a stalled
    event loop that delays task start is counted (no coordinated omission)."""
    lat = []

    async def one(idx, planned):
        kwargs = {"litellm_metadata": {"user_api_key_metadata": {"labels": [key_labels[idx % len(key_labels)]]}}, "input": "x"}
        await router.async_get_available_deployment(model=f"m{idx % MODELS_PER_SUB}", request_kwargs=kwargs, input="x")
        lat.append((time.perf_counter() - planned) * 1000)

    tasks = []
    start = time.perf_counter()
    for idx in range(int(rate * seconds)):
        planned = start + idx / rate
        delay = planned - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        tasks.append(asyncio.create_task(one(idx, planned)))
    await asyncio.gather(*tasks)
    return {"p50_ms": round(statistics.median(lat), 2), "p95_ms": round(pct(lat, 0.95), 2), "p99_ms": round(pct(lat, 0.99), 2),
            "max_ms": round(max(lat), 2), "calls": len(lat), "rate_rps": rate}


async def main():
    results = []
    rcli = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    for subs in (30, 90):  # 600 and 1800 deployments
        deployments = subs * MODELS_PER_SUB
        await rcli.flushall()
        t0 = time.time()
        router = Router(model_list=build_model_list(subs), redis_host=REDIS_HOST, redis_port=REDIS_PORT,
                        enable_weighted_failover=True, routing_strategy="simple-shuffle")
        build_s = round(time.time() - t0, 1)
        sub_names = [f"sub-{s}" for s in range(subs)]
        key_labels = [f"employee:{i}" for i in range(KEY_COUNT)]
        policy = {label: set(random.sample(sub_names, k=max(3, subs // 3))) for label in key_labels}
        # state keys for the filter (some subs "limited") and 15% of deployments in redis cooldown
        pipe = rcli.pipeline()
        for sub in sub_names:
            pipe.set(f"agentek:sub:{sub}:win", random.randint(0, 100))
            if random.random() < 0.15:
                pipe.set(f"agentek:sub:{sub}:state", "SOFT_LIMITED")
        await pipe.execute()
        all_ids = [d["model_info"]["id"] for d in router.model_list]
        for dep_id in random.sample(all_ids, k=len(all_ids) // 6):
            router.cooldown_cache.add_deployment_to_cooldown(model_id=dep_id, original_exception=Exception("x"), exception_status=429, cooldown_time=3600)
        filter_modes = os.environ.get("FILTER_MODES", "off,mget").split(",")
        for mode in filter_modes:
            with_filter = mode != "off"
            if mode == "snapshot":
                snap = SnapshotFilter(rcli, sub_names, policy)
                refresher = asyncio.create_task(snap.refresh_forever())
                await asyncio.sleep(0.2)
                litellm.callbacks = [snap]
            else:
                litellm.callbacks = [PolicyFilter(rcli, sub_names, policy)] if with_filter else []
            for concurrency in (1, 32):
                res = await measure(router, concurrency, key_labels)
                row = {"deployments": deployments, "group_size": subs, "filter": mode, "concurrency": concurrency, "router_build_s": build_s, **res}
                print(json.dumps(row), flush=True)
                results.append(row)
            for rate in (50, 100, 200):
                res = await measure_open_loop(router, rate, key_labels)
                row = {"deployments": deployments, "group_size": subs, "filter": mode, "open_loop": True, **res}
                print(json.dumps(row), flush=True)
                results.append(row)
            if mode == "snapshot":
                refresher.cancel()
        del router
    json.dump(results, open(os.environ.get("OUT", "/spikes/logs/0.6_results.json"), "w"), indent=1)


asyncio.run(main())
