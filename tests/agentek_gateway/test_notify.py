import asyncio
import logging
import time

import fakeredis
import pytest

from agentek_gateway.subscriptions.notify import RedisListener, RedisNotifier
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.state_db import InMemoryStateDb

from .conftest import FakeClock
from .live import needs_redis
from .test_redis_state import record

CHANNEL = "t:changes"
DELIVERY_BUDGET_S = 1.0


class Counter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


async def wait_for(counter: Counter, at_least: int = 1) -> float:
    started = time.monotonic()
    while counter.count < at_least and time.monotonic() - started < 3:
        await asyncio.sleep(0.005)
    return time.monotonic() - started


async def test_listener_reports_each_published_change() -> None:
    server = fakeredis.FakeServer()
    redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    counter = Counter()
    task = asyncio.get_running_loop().create_task(
        RedisListener(redis, CHANNEL, counter).run()
    )
    try:
        await asyncio.sleep(0.05)
        notifier = RedisNotifier(redis, CHANNEL)

        await notifier.publish()
        await notifier.publish()
        await wait_for(counter, 2)
    finally:
        task.cancel()

    assert counter.count == 2


async def test_state_write_on_one_replica_notifies_the_listener_of_another() -> None:
    server = fakeredis.FakeServer()
    writer = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    reader = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    keys = Keys("t:")
    store = RedisStateStore(
        writer,
        InMemoryStateDb(),
        FakeClock(),
        keys,
        RedisNotifier(writer, keys.changes),
    )
    counter = Counter()
    task = asyncio.get_running_loop().create_task(
        RedisListener(reader, keys.changes, counter).run()
    )
    try:
        await asyncio.sleep(0.05)

        await store.compare_and_set_state("a", None, record())
        delay = await wait_for(counter)
    finally:
        task.cancel()

    assert (counter.count, delay < DELIVERY_BUDGET_S) == (1, True)


async def test_listener_resubscribes_after_redis_comes_back() -> None:
    server = fakeredis.FakeServer()
    redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    counter = Counter()
    task = asyncio.get_running_loop().create_task(
        RedisListener(redis, CHANNEL, counter).run()
    )
    try:
        await asyncio.sleep(0.05)
        server.connected = False
        await asyncio.sleep(0.1)
        server.connected = True
        await asyncio.sleep(1.3)

        await RedisNotifier(redis, CHANNEL).publish()
        await wait_for(counter)
    finally:
        task.cancel()

    assert counter.count == 1


async def test_failed_publish_does_not_raise() -> None:
    server = fakeredis.FakeServer()
    redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    server.connected = False

    await RedisNotifier(redis, CHANNEL).publish()


@needs_redis
async def test_an_idle_channel_is_not_an_error_and_a_late_notification_still_arrives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from redis.asyncio import Redis

    from .live import REDIS_URL

    redis = Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=0.4)  # type: ignore[arg-type]
    counter = Counter()
    task = asyncio.get_running_loop().create_task(
        RedisListener(redis, CHANNEL, counter).run()
    )
    try:
        with caplog.at_level(logging.ERROR):
            await asyncio.sleep(1.5)
            await RedisNotifier(redis, CHANNEL).publish()
            await wait_for(counter)
    finally:
        task.cancel()
        await redis.aclose()

    assert (
        counter.count,
        [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR],
    ) == (1, [])
