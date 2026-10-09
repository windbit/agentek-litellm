import asyncio
import time

import pytest

from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
from agentek_gateway.subscriptions.events import (
    LimitExhausted,
    LimitWindow,
    OperatorDisabled,
)
from agentek_gateway.subscriptions.service import StateService
from agentek_gateway.subscriptions.snapshot import SnapshotTiming

from .conftest import make_subscription
from .plain import SHARED_ID, deployment, plain_runtime

HOUR_S = 3600.0
STALE_AFTER_S = 60.0
DIRECTORY_TTL_S = SnapshotTiming().directory_ttl_s


async def test_subscriptions_are_closed_until_the_first_load() -> None:
    plain = plain_runtime(["a", "b"], shared=True)

    assert (plain.runtime.parts.snapshot.current, await plain.pick()) == (
        None,
        [SHARED_ID],
    )


async def test_group_of_subscriptions_only_is_refused_before_the_first_load() -> None:
    plain = plain_runtime(["a"])

    with pytest.raises(NoAvailableSubscriptionsError):
        await plain.pick()


async def test_loaded_snapshot_serves_subscriptions() -> None:
    plain = plain_runtime(["a", "b"], shared=True)
    await plain.runtime.parts.snapshot.refresh()

    assert await plain.pick() == ["sub:a:gpt-x", SHARED_ID]


async def test_ten_blocked_of_twelve_leave_only_the_live_ones_after_flushall() -> None:
    sub_ids = [f"s{index:02d}" for index in range(12)]
    plain = plain_runtime(sub_ids)
    await plain.store.load_durable()
    states = plain.runtime.parts.failures
    del states
    from agentek_gateway.subscriptions.service import StateService

    service = StateService(plain.store, plain.clock, plain.runtime.parts.config)
    subs = {
        sub.id: sub
        for sub in await plain.runtime.parts.signals._repo.list_subscriptions()
    }  # noqa: SLF001
    for sub_id in sub_ids[:10]:
        await service.apply(
            subs[sub_id], LimitExhausted(LimitWindow.WEEKLY, plain.clock.now() + HOUR_S)
        )
    await plain.redis.flushall()
    await plain.runtime.parts.snapshot.refresh()

    picks = {tuple(await plain.pick(f"req{index}")) for index in range(5)}

    assert picks == {("sub:s10:gpt-x",)}


async def test_decisions_use_the_last_snapshot_for_a_minute_without_redis_then_stop() -> (
    None
):
    plain = plain_runtime(["a"], shared=True)
    await plain.runtime.parts.snapshot.refresh()
    plain.server.connected = False

    with pytest.raises(Exception):  # noqa: B017, PT011
        await plain.runtime.parts.snapshot.refresh()
    plain.clock.advance(STALE_AFTER_S - 1)
    within = await plain.pick()
    plain.clock.advance(2)
    after = await plain.pick()

    assert (within, after) == (["sub:a:gpt-x", SHARED_ID], [SHARED_ID])


async def test_subscription_only_group_gets_429_once_the_snapshot_is_stale() -> None:
    plain = plain_runtime(["a"])
    await plain.runtime.parts.snapshot.refresh()
    plain.clock.advance(STALE_AFTER_S + 1)

    with pytest.raises(NoAvailableSubscriptionsError) as raised:
        await plain.pick()

    assert raised.value.status_code == 429


async def test_state_change_shows_up_after_the_snapshot_interval() -> None:
    plain = plain_runtime(["a", "b"], timing=SnapshotTiming(interval_s=0.05))
    snapshot = plain.runtime.parts.snapshot
    runner = asyncio.get_running_loop().create_task(snapshot.run())
    try:
        await asyncio.sleep(0.1)
        before = await plain.pick()
        sub_a = make_subscription("a")
        await StateService(plain.store, plain.clock, plain.runtime.parts.config).apply(
            sub_a, OperatorDisabled()
        )
        await asyncio.sleep(0.2)
        after = await plain.pick("req2")
    finally:
        runner.cancel()

    assert (before, after) == (["sub:a:gpt-x"], ["sub:b:gpt-x"])


async def test_refresh_request_wakes_the_loop_before_the_interval() -> None:
    plain = plain_runtime(["a"], timing=SnapshotTiming(interval_s=30.0))
    snapshot = plain.runtime.parts.snapshot
    runner = asyncio.get_running_loop().create_task(snapshot.run())
    try:
        await asyncio.sleep(0.05)
        first = snapshot.current
        plain.clock.advance(1)
        snapshot.request_refresh()
        started = time.monotonic()
        while snapshot.current is first and time.monotonic() - started < 1:
            await asyncio.sleep(0.01)
        second = snapshot.current
    finally:
        runner.cancel()

    assert second is not first


async def test_new_subscription_reaches_the_snapshot_within_the_directory_ttl() -> None:
    plain = plain_runtime(["a"], shared=True)
    snapshot = plain.runtime.parts.snapshot
    await snapshot.refresh()
    plain.runtime.parts.snapshot._sources.repo.put(
        make_subscription("b")
    )  # noqa: SLF001
    plain.deployments.append(deployment("b"))

    plain.clock.advance(DIRECTORY_TTL_S - 1)
    early = sorted((await snapshot.refresh()).subscriptions)
    plain.clock.advance(2)
    late = sorted((await snapshot.refresh()).subscriptions)

    assert (early, late) == (["a"], ["a", "b"])


async def test_chat_binding_store_outage_does_not_stop_routing_on_the_last_snapshot() -> (
    None
):
    plain = plain_runtime(["a"], shared=True)
    await plain.runtime.parts.snapshot.refresh()
    plain.server.connected = False

    picked = await plain.pick(prompt_cache_key="chat-1")

    assert picked == ["sub:a:gpt-x", SHARED_ID]
