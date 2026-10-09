"""0.6 (profile): where does the selection time go at 1800 deployments? Env: SUBS, COOLED (fraction of deployments in cooldown), TOP."""
import asyncio, cProfile, io, os, pstats, random, statistics, time

import redis.asyncio as aioredis
from litellm import Router

SUBS, COOLED = int(os.environ.get("SUBS", "90")), float(os.environ.get("COOLED", "0.17"))
MODELS = 20


def model_list():
    return [{"model_name": f"m{m}", "litellm_params": {"model": "chatgpt/gpt-5.4", "litellm_credential_name": f"sub-{s}",
             "chatgpt_auth": {"access_token": "x", "expires_at": 4102444800, "account_id": f"a{s}"}},
             "model_info": {"id": f"sub:sub-{s}:m{m}", "mode": "responses"}} for s in range(SUBS) for m in range(MODELS)]


async def main():
    await aioredis.Redis(host="poolspike-redis").flushall()
    router = Router(model_list=model_list(), redis_host="poolspike-redis", redis_port=6379, enable_weighted_failover=True)
    ids = [d["model_info"]["id"] for d in router.model_list]
    for dep_id in random.sample(ids, k=int(len(ids) * COOLED)):
        router.cooldown_cache.add_deployment_to_cooldown(model_id=dep_id, original_exception=Exception("x"), exception_status=429, cooldown_time=3600)
    async def one(i):
        return await router.async_get_available_deployment(model=f"m{i % MODELS}", request_kwargs={"input": "x"}, input="x")
    for i in range(100):
        await one(i)
    lat = []
    for i in range(300):
        t0 = time.perf_counter(); await one(i); lat.append((time.perf_counter() - t0) * 1000)
    print(f"deployments={SUBS*MODELS} cooled={COOLED} p50={statistics.median(lat):.2f}ms p99={sorted(lat)[int(len(lat)*.99)]:.2f}ms")
    if os.environ.get("PROFILE"):
        prof = cProfile.Profile(); prof.enable()
        for i in range(200):
            await one(i)
        prof.disable()
        out = io.StringIO(); pstats.Stats(prof, stream=out).sort_stats("cumulative").print_stats(14)
        print("\n".join(l for l in out.getvalue().splitlines() if l.strip())[:3500])

asyncio.run(main())
