import asyncio

import pytest

from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
from agentek_gateway.subscriptions.gateway import SubscriptionBusyError

from .stack import MODEL, Shared, account_of, running_stack
from .test_adapter_hooks import attempt_kwargs

PROVIDER_LIMIT = 2
SUBSCRIPTION_LIMIT = 3


def limited(limit: int) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {"providers": {"chatgpt": {"concurrency_limit": limit}}}
    )


async def try_reserve(stack, request_id: str) -> bool:  # type: ignore[no-untyped-def]
    try:
        await stack.callback.async_pre_call_deployment_hook(
            attempt_kwargs(request_id=request_id), None
        )
    except SubscriptionBusyError:
        return False
    return True


async def in_flight(stack) -> int:  # type: ignore[no-untyped-def]
    return (await stack.slot_store.in_flight(["a"]))["a"]


async def test_without_a_configured_limit_the_subscription_is_unlimited() -> None:
    async with running_stack(["a"]) as stack:
        results = [await try_reserve(stack, f"r{index}") for index in range(60)]

        assert all(results)


async def test_provider_default_limit_applies_to_a_subscription_without_its_own() -> (
    None
):
    async with running_stack(["a"], config=limited(PROVIDER_LIMIT)) as stack:
        results = [await try_reserve(stack, f"r{index}") for index in range(4)]

        assert results == [True, True, False, False]


async def test_subscription_limit_overrides_the_provider_default() -> None:
    async with running_stack(
        ["a"], config=limited(PROVIDER_LIMIT), slot_limit=SUBSCRIPTION_LIMIT
    ) as stack:
        results = [await try_reserve(stack, f"r{index}") for index in range(5)]

        assert results.count(True) == SUBSCRIPTION_LIMIT


async def test_two_replicas_never_exceed_the_limit_together() -> None:
    shared = Shared()
    async with (
        running_stack(["a"], shared=shared, slot_limit=4) as first,
        running_stack(["a"], shared=shared, slot_limit=4) as second,
    ):
        outcomes = await asyncio.gather(
            *(try_reserve(first, f"first-{index}") for index in range(3)),
            *(try_reserve(second, f"second-{index}") for index in range(3)),
        )

        assert (outcomes.count(True), await in_flight(first)) == (4, 4)


async def test_slot_freed_on_one_replica_is_usable_on_the_other() -> None:
    shared = Shared()
    async with (
        running_stack(["a"], shared=shared, slot_limit=1) as first,
        running_stack(["a"], shared=shared, slot_limit=1) as second,
    ):
        assert await try_reserve(first, "r1")
        assert not await try_reserve(second, "r2")

        await first.runtime.parts.ledger.finish("r1")

        assert await try_reserve(second, "r2")


async def test_requests_over_the_limit_get_the_429_and_the_rest_are_served() -> None:
    async with running_stack(["a"], slot_limit=2) as stack:
        stack.mock.script(
            account_of("a"), *(["slow_stream"] * 4), default="slow_stream"
        )

        outcomes = await asyncio.gather(
            *(stack.call() for _ in range(4)), return_exceptions=True
        )

        served = [item for item in outcomes if not isinstance(item, BaseException)]
        refused = [
            item for item in outcomes if isinstance(item, NoAvailableSubscriptionsError)
        ]
        assert (len(served), len(refused)) == (2, 2)


async def test_fifty_clients_leaving_mid_stream_leave_no_slots_behind() -> None:
    async with running_stack(["a"]) as stack:
        stack.mock.script(account_of("a"), default="slow_stream")
        streams = [await stack.open_stream() for _ in range(10)]
        for stream in streams:
            await anext(stream)
        assert await in_flight(stack) == 10

        for stream in streams:
            await stream.aclose()
        await stack.finished()

        assert await in_flight(stack) == 0


async def test_releasing_one_attempt_twice_frees_one_slot() -> None:
    async with running_stack(["a"], slot_limit=2) as stack:
        await try_reserve(stack, "r1")
        await try_reserve(stack, "r2")
        reservation = stack.runtime.parts.ledger.active("r1")
        assert reservation is not None

        first = await stack.runtime.parts.ledger.release(reservation)
        second = await stack.runtime.parts.ledger.release(reservation)

        assert (first, second, await in_flight(stack)) == (True, False, 1)


async def test_unfinished_requests_free_their_slots_after_the_ttl() -> None:
    async with running_stack(["a"], slot_limit=1) as stack:
        assert await try_reserve(stack, "r1")
        assert not await try_reserve(stack, "r2")

        stack.clock.advance(GatewayConfig().defaults.slot_ttl_s + 1)

        assert await try_reserve(stack, "r3")
    _ = (pytest, MODEL)
