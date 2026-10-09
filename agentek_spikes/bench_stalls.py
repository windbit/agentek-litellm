"""0.6 (stalls): are the p99 spikes the 1 s Redis refresh of the router cooldown cache? Prints outlier timestamps at 100 rps, no plugin filter."""
import asyncio, os, random, statistics, time
import redis.asyncio as aioredis
from litellm import Router

SUBS, MODELS = int(os.environ.get("SUBS", "90")), 20
RATE, SECONDS = 100, 12

async def main():
    await aioredis.Redis(host="poolspike-redis").flushall()
    ml = [{"model_name": f"m{m}", "litellm_params": {"model": "chatgpt/gpt-5.4", "litellm_credential_name": f"sub-{s}", "chatgpt_auth": {"access_token": "x", "expires_at": 4102444800, "account_id": f"a{s}"}},
           "model_info": {"id": f"sub:sub-{s}:m{m}", "mode": "responses"}} for s in range(SUBS) for m in range(MODELS)]
    router = Router(model_list=ml, redis_host="poolspike-redis", redis_port=6379)
    out = []
    async def one(i, t_start):
        t0 = time.perf_counter()
        await router.async_get_available_deployment(model=f"m{i % MODELS}", request_kwargs={"input": "x"}, input="x")
        out.append((t0 - t_start, (time.perf_counter() - t0) * 1000))
    t_start = time.perf_counter(); tasks = []
    for i in range(RATE * SECONDS):
        d = t_start + i / RATE - time.perf_counter()
        if d > 0:
            await asyncio.sleep(d)
        tasks.append(asyncio.create_task(one(i, t_start)))
    await asyncio.gather(*tasks)
    lat = [l for _, l in out]
    print(f"deployments={SUBS*MODELS} rate={RATE}rps p50={statistics.median(lat):.1f} p99={sorted(lat)[int(len(lat)*.99)]:.1f} max={max(lat):.1f}")
    slow = sorted((t, l) for t, l in out if l > 15)
    print("slow calls (>15 ms), start offsets in s:", [f"{t:.2f}:{l:.0f}ms" for t, l in slow[:24]])

asyncio.run(main())
