# ruff: noqa: T201
"""Closed-loop HTTP driver; runs inside a container of the gateway image (has aiohttp). Prints one JSON line."""

import asyncio
import collections
import json
import sys
import time

import aiohttp

cfg = json.loads(sys.argv[1])
URLS = cfg["urls"]
MODEL = cfg.get("model", "gpt-5.4")
KEY = cfg.get("key", "sk-loadtest-master")
REQUESTS = cfg.get("n")
DURATION_S = cfg.get("duration")
CONCURRENCY = cfg.get("conc", 10)
API = cfg.get("api", "chat")
CACHE_KEYS = cfg.get("cache_keys")
REQUEST_TIMEOUT_S = 120

status: collections.Counter = collections.Counter()
retry_after: collections.Counter = collections.Counter()
latencies: list[float] = []
samples: dict[str, str] = {}
events: list[list[object]] = []
started = time.time()


def request_of(index: int) -> tuple[str, dict[str, object]]:
    if API == "responses":
        body: dict[str, object] = {"model": MODEL, "input": "hi", "stream": False}
        if CACHE_KEYS:
            body["prompt_cache_key"] = CACHE_KEYS[index % len(CACHE_KEYS)]
        return "/v1/responses", body
    return "/v1/chat/completions", {
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
    }


async def one(session: aiohttp.ClientSession, index: int) -> None:
    path, body = request_of(index)
    url = URLS[index % len(URLS)] + path
    began, wall = time.perf_counter(), time.time()
    try:
        async with session.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {KEY}"},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
        ) as reply:
            text = await reply.text()
            code = str(reply.status)
            if reply.status != 200:
                retry_after[str(reply.headers.get("retry-after"))] += 1
                samples.setdefault(code, text[:300])
    except Exception as error:  # noqa: BLE001
        code = "EXC:" + type(error).__name__
        samples.setdefault(code, str(error)[:200])
    elapsed = time.perf_counter() - began
    status[code] += 1
    latencies.append(elapsed)
    if cfg.get("log"):
        events.append([round(wall, 3), code, round(elapsed * 1000)])


async def worker(session: aiohttp.ClientSession, queue: asyncio.Queue) -> None:
    while (index := await queue.get()) is not None:
        await one(session, index)


async def main() -> None:
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=0)
    ) as session:
        queue: asyncio.Queue = asyncio.Queue()
        workers = [
            asyncio.create_task(worker(session, queue)) for _ in range(CONCURRENCY)
        ]
        if DURATION_S:
            end, index = time.time() + DURATION_S, 0
            while time.time() < end:
                if queue.qsize() < CONCURRENCY:
                    await queue.put(index)
                    index += 1
                else:
                    await asyncio.sleep(0.001)
        else:
            for index in range(REQUESTS):
                await queue.put(index)
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)


asyncio.run(main())
latencies.sort()


def percentile(share: float) -> float | None:
    if not latencies:
        return None
    return round(
        latencies[min(len(latencies) - 1, int(len(latencies) * share))] * 1000, 1
    )


print(
    json.dumps(
        {
            "total": sum(status.values()),
            "status": dict(status),
            "retry_after": dict(retry_after),
            "p50_ms": percentile(0.5),
            "p99_ms": percentile(0.99),
            "rps": round(sum(status.values()) / (time.time() - started), 1),
            "samples": samples,
            "events": events if cfg.get("log") else None,
        }
    )
)
