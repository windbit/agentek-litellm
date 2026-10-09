import fakeredis
import pytest

from agentek_gateway.subscriptions.redis_slots import RedisSlotStore
from agentek_gateway.subscriptions.slots import (
    InMemorySlotStore,
    ReserveRequest,
    SlotLedger,
)

from .conftest import FakeClock, make_subscription

TTL_S = 900.0
SUB = make_subscription("a")


def request_for(
    request_id: str, deployment_id: str, limit: int | None = None
) -> ReserveRequest:
    return ReserveRequest(
        request_id=request_id,
        deployment_id=deployment_id,
        subscription=SUB,
        model_group="m",
        alternatives=0,
        limit=limit,
    )


@pytest.fixture(params=["memory", "redis"])
def store(request: pytest.FixtureRequest, clock: FakeClock):  # type: ignore[no-untyped-def]
    if request.param == "memory":
        return InMemorySlotStore(clock)
    return RedisSlotStore(fakeredis.FakeAsyncRedis(), clock, "t:")


async def test_limit_is_enforced_per_subscription(store) -> None:  # type: ignore[no-untyped-def]
    results = [await store.reserve("a", f"t{index}", 2, TTL_S) for index in range(3)]
    assert (results, (await store.in_flight(["a"]))["a"]) == ([True, True, False], 2)


async def test_no_limit_means_no_refusal(store) -> None:  # type: ignore[no-untyped-def]
    results = [
        await store.reserve("a", f"t{index}", None, TTL_S) for index in range(50)
    ]
    assert all(results)


async def test_release_is_idempotent_and_never_goes_below_zero(store) -> None:  # type: ignore[no-untyped-def]
    await store.reserve("a", "t1", None, TTL_S)

    released = [await store.release("a", "t1"), await store.release("a", "t1")]

    assert (released, (await store.in_flight(["a"]))["a"]) == ([True, False], 0)


async def test_abandoned_slot_expires_after_the_ttl(store, clock) -> None:  # type: ignore[no-untyped-def]
    await store.reserve("a", "t1", 1, TTL_S)
    clock.advance(TTL_S + 1)

    assert await store.reserve("a", "t2", 1, TTL_S)


async def test_two_stores_on_one_redis_share_the_limit(clock: FakeClock) -> None:
    redis = fakeredis.FakeAsyncRedis()
    first, second = (RedisSlotStore(redis, clock, "t:") for _ in range(2))

    granted = [
        await first.reserve("a", "r1", 4, TTL_S),
        await second.reserve("a", "r2", 4, TTL_S),
        await first.reserve("a", "r3", 4, TTL_S),
        await second.reserve("a", "r4", 4, TTL_S),
        await first.reserve("a", "r5", 4, TTL_S),
        await second.reserve("a", "r6", 4, TTL_S),
    ]

    assert (granted.count(True), (await first.in_flight(["a"]))["a"]) == (4, 4)


async def test_ledger_reserves_once_per_request_and_deployment(
    clock: FakeClock,
) -> None:
    slots = InMemorySlotStore(clock)
    ledger = SlotLedger(slots, clock, TTL_S)

    first = await ledger.reserve(request_for("r", "d1"))
    again = await ledger.reserve(request_for("r", "d1"))

    assert (first is again, (await slots.in_flight(["a"]))["a"]) == (True, 1)


async def test_ledger_double_release_of_one_token_frees_one_slot(
    clock: FakeClock,
) -> None:
    slots = InMemorySlotStore(clock)
    ledger = SlotLedger(slots, clock, TTL_S)
    await slots.reserve("a", "someone-else", None, TTL_S)
    reservation = await ledger.reserve(request_for("r", "d1"))
    assert reservation is not None

    released = [await ledger.release(reservation), await ledger.release(reservation)]

    assert (released, (await slots.in_flight(["a"]))["a"]) == ([True, False], 1)


async def test_moving_to_the_next_deployment_releases_the_previous_slot(
    clock: FakeClock,
) -> None:
    slots = InMemorySlotStore(clock)
    ledger = SlotLedger(slots, clock, TTL_S)
    await ledger.reserve(request_for("r", "d1"))

    second = await ledger.reserve(request_for("r", "d2"))

    assert second is not None
    assert ((await slots.in_flight(["a"]))["a"], second.attempt) == (1, 2)


async def test_finish_releases_everything_the_request_holds(clock: FakeClock) -> None:
    slots = InMemorySlotStore(clock)
    ledger = SlotLedger(slots, clock, TTL_S)
    await ledger.reserve(request_for("r", "d1"))

    await ledger.finish("r")
    await ledger.finish("r")

    assert ((await slots.in_flight(["a"]))["a"], ledger.size()) == (0, 0)


async def test_refused_reservation_is_not_remembered(clock: FakeClock) -> None:
    slots = InMemorySlotStore(clock)
    ledger = SlotLedger(slots, clock, TTL_S)
    await slots.reserve("a", "other", 1, TTL_S)

    refused = await ledger.reserve(request_for("r", "d1", limit=1))

    assert (refused, ledger.find("r", "d1")) == (None, None)


async def test_ledger_forgets_requests_after_the_ttl(clock: FakeClock) -> None:
    ledger = SlotLedger(InMemorySlotStore(clock), clock, TTL_S)
    await ledger.reserve(request_for("r", "d1"))

    clock.advance(TTL_S + 1)

    assert ledger.size() == 0
