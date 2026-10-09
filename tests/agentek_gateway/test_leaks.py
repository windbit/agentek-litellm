"""Slots and bookkeeping must come back on every way a request can end, and the bookkeeping must stay bounded."""

from dataclasses import replace

import pytest

from agentek_gateway.subscriptions.expiring import ExpiringMap
from agentek_gateway.subscriptions.gateway import SubscriptionBusyError
from agentek_gateway.subscriptions.outcomes import OutcomeTracker
from agentek_gateway.subscriptions.providers.observer import current_attempt
from agentek_gateway.subscriptions.slots import (
    InMemorySlotStore,
    ReserveRequest,
    SlotLedger,
)

from .conftest import FakeClock, make_subscription
from .stack import Shared, running_stack
from .test_adapter_hooks import attempt_kwargs

TTL_S = 900.0


async def in_flight(stack) -> int:  # type: ignore[no-untyped-def]
    return (await stack.slot_store.in_flight(["a"]))["a"]


async def test_failed_slot_reservation_excludes_the_subscription_instead_of_running_unprotected() -> (
    None
):
    shared = Shared()
    async with running_stack(["a"], shared=shared) as stack:
        shared.server.connected = False

        with pytest.raises(SubscriptionBusyError):
            await stack.callback.async_pre_call_deployment_hook(attempt_kwargs(), None)

        shared.server.connected = True
        assert (
            current_attempt.get(),
            stack.runtime.parts.attempts.attempted("r1"),
        ) == (None, frozenset({"sub:a:gpt-5.4"}))


async def test_success_bookkeeping_that_raises_still_frees_the_request() -> None:
    class Exploding:
        async def on_success(self, subscription, limits):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    async with running_stack(["a"]) as stack:
        kwargs = attempt_kwargs()
        await stack.callback.async_pre_call_deployment_hook(kwargs, None)
        parts = replace(stack.runtime.parts, signals=Exploding())  # type: ignore[arg-type]

        with pytest.raises(RuntimeError):
            await OutcomeTracker(parts).on_success(kwargs, None)

        assert (await in_flight(stack), stack.runtime.parts.ledger.size()) == (0, 0)


async def test_non_streaming_success_frees_the_slot_even_if_the_log_event_never_comes() -> (
    None
):
    async with running_stack(["a"]) as stack:
        await stack.callback.async_pre_call_deployment_hook(attempt_kwargs(), None)
        data = {"metadata": {"agentek_request_id": "r1"}, "stream": False}

        changed = await stack.callback.async_post_call_success_hook(data, None, object())  # type: ignore[arg-type]

        assert (changed, await in_flight(stack)) == (None, 0)


async def test_streaming_response_keeps_its_slot_at_the_success_hook() -> None:
    async with running_stack(["a"]) as stack:
        await stack.callback.async_pre_call_deployment_hook(attempt_kwargs(), None)
        data = {"metadata": {"agentek_request_id": "r1"}, "stream": True}

        await stack.callback.async_post_call_success_hook(data, None, object())  # type: ignore[arg-type]

        assert await in_flight(stack) == 1


async def test_slot_of_a_stream_longer_than_the_ttl_is_kept_alive_while_chunks_arrive() -> (
    None
):
    async with running_stack(["a"], slot_limit=1) as stack:
        await stack.callback.async_pre_call_deployment_hook(attempt_kwargs(), None)

        async def upstream():  # type: ignore[no-untyped-def]
            for _ in range(4):
                yield "chunk"
                stack.clock.advance(TTL_S / 2.2)

        watched = stack.runtime.outcomes.watch_stream(
            {"metadata": {"agentek_request_id": "r1"}}, upstream()
        )
        seen = [chunk async for chunk in watched]

        assert (len(seen), await in_flight(stack)) == (4, 1)
        await stack.finished()


def test_overwritten_entry_leaves_a_stale_heap_record_that_cannot_evict_the_live_value() -> (
    None
):
    clock = FakeClock()
    cache: ExpiringMap[str, int] = ExpiringMap(clock, TTL_S, max_entries=2)
    cache.put("a", 1)
    cache.put("a", 2)
    cache.put("b", 3)
    cache.put("c", 4)

    assert (cache.get("c"), cache.get("b")) == (4, 3)


def test_heap_stays_proportional_to_the_live_entries() -> None:
    cache: ExpiringMap[str, int] = ExpiringMap(FakeClock(), TTL_S)

    for index in range(20_000):
        cache.put("same", index)

    assert len(cache._expiry) < 5_000  # noqa: SLF001


async def test_ledger_drops_the_oldest_requests_when_it_is_full() -> None:
    clock = FakeClock()
    slots = InMemorySlotStore(clock)
    ledger = SlotLedger(slots, clock, TTL_S, max_requests=3)
    subscription = make_subscription("a")

    for index in range(5):
        clock.advance(1)
        await ledger.reserve(
            ReserveRequest(f"r{index}", "d", subscription, "m", 0, None)
        )

    assert (
        ledger.size(),
        ledger.find("r0", "d"),
        ledger.find("r4", "d") is not None,
    ) == (
        3,
        None,
        True,
    )


def test_refreshed_entry_survives_overflow_that_pops_its_old_heap_record() -> None:
    cache: ExpiringMap[str, int] = ExpiringMap(FakeClock(), TTL_S, max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 3)
    cache.put("c", 4)

    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (3, None, 4)


async def test_extending_a_released_slot_does_not_bring_it_back() -> None:
    import fakeredis

    from agentek_gateway.subscriptions.redis_slots import RedisSlotStore

    clock = FakeClock()
    for store in (
        InMemorySlotStore(clock),
        RedisSlotStore(fakeredis.FakeAsyncRedis(decode_responses=True), clock, "t:"),
    ):
        await store.reserve("a", "token", None, TTL_S)
        await store.release("a", "token")

        extended = await store.extend("a", "token", TTL_S)

        assert (extended, (await store.in_flight(["a"]))["a"]) == (False, 0)
