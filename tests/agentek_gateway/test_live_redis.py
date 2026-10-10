"""Atomicity that only a real Redis shows: contention, WATCH, pub/sub, MGET, TTLs."""

import asyncio
import logging

import pytest
from redis.asyncio import Redis
import redis as sync_redis_module

from litellm._logging import verbose_proxy_logger

from agentek_gateway.subscriptions.leader import LeaderLease
from agentek_gateway.subscriptions.model import (
    Limits,
    UsageRecord,
    Window,
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState as S,
)
from agentek_gateway.subscriptions.notify import RedisListener, RedisNotifier
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_slots import RedisSlotStore
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.state_codec import decode_record, encode_record
from agentek_gateway.subscriptions.state_db import InMemoryStateDb
from agentek_gateway.subscriptions.token_coordination import (
    SyncTokenCoordinator,
    TokenCoordinator,
)

from .conftest import FakeClock
from .live import (
    REDIS_URL,
    InterferingRedis,
    SyncInterferingRedis,
    live_redis,
    needs_redis,
)

pytestmark = needs_redis

KEYS = Keys("live:")
CREDENTIAL = "cred-live"


def record(
    state: S = S.ACTIVE, version: int = 1, until: float | None = None
) -> StateRecord:
    return StateRecord(
        state, version, 1_000_000.0, until, StateReason.NONE, SignalSource.NONE, 0
    )


def store_on(redis, clock, db=None):  # type: ignore[no-untyped-def]
    return RedisStateStore(redis, db or InMemoryStateDb(), clock, KEYS)


async def test_only_one_of_many_concurrent_writers_wins_the_same_version() -> None:
    async with live_redis() as redis:
        store = store_on(redis, FakeClock())

        results = await asyncio.gather(
            *(
                store.compare_and_set_state("a", None, record(S.BANNED, 1))
                for _ in range(8)
            )
        )

        assert results.count(True) == 1


async def test_write_is_refused_when_the_key_changes_between_the_read_and_the_write() -> (
    None
):
    async with live_redis() as redis:
        clock = FakeClock()
        await store_on(redis, clock).compare_and_set_state(
            "a", None, record(S.ACTIVE, 1)
        )

        async def intruder() -> None:
            await redis.set(KEYS.state("a"), encode_record(record(S.BANNED, 2)))

        racing = store_on(InterferingRedis(redis, intruder), clock)  # type: ignore[arg-type]

        won = await racing.compare_and_set_state("a", 1, record(S.RATE_LIMITED, 2))

        stored = decode_record(await redis.get(KEYS.state("a")))
        assert (won, stored.state) == (False, S.BANNED)


async def test_state_key_expires_with_the_block_and_unset_ones_read_as_missing() -> (
    None
):
    async with live_redis() as redis:
        clock = FakeClock()
        store = store_on(redis, clock)
        await store.compare_and_set_state(
            "a", None, record(S.RATE_LIMITED, 1, clock.now() + 5)
        )

        ttl = await redis.ttl(KEYS.state("a"))
        states = await store.read_states(["a", "b"])

        assert (5 < ttl <= 65, sorted(states)) == (True, ["a"])


async def test_batch_read_returns_exactly_the_requested_existing_ids() -> None:
    async with live_redis() as redis:
        store = store_on(redis, FakeClock())
        for sub_id in ("a", "c"):
            await store.compare_and_set_state(sub_id, None, record(S.BANNED, 1))
        await store.compare_and_set_state("other", None, record(S.BANNED, 1))

        states = await store.read_states(["a", "b", "c"])

        assert sorted(states) == ["a", "c"]


async def test_series_counters_count_in_a_real_sorted_set_window() -> None:
    async with live_redis() as redis:
        store = store_on(redis, FakeClock())

        counts = [await store.record_unclassified("a", 120.0) for _ in range(3)]
        await store.reset_series("a")
        after = await store.record_unclassified("a", 120.0)

        assert (
            counts,
            after,
            (await store.unclassified_counts(["a"], 120.0))["a"],
        ) == (
            [1, 2, 3],
            1,
            4,
        )


async def test_lease_has_one_owner_among_concurrent_holders() -> None:
    async with live_redis() as redis:
        leases = [LeaderLease(redis, KEYS.leader) for _ in range(8)]

        results = await asyncio.gather(*(lease.hold() for lease in leases))

        assert results.count(True) == 1


async def test_lease_is_renewed_only_by_its_owner() -> None:
    async with live_redis() as redis:
        owner, other = LeaderLease(redis, KEYS.leader, ttl_s=30), LeaderLease(
            redis, KEYS.leader, ttl_s=300
        )
        await owner.hold()

        refused = await other.hold()

        assert (refused, await redis.ttl(KEYS.leader) <= 30) == (False, True)


async def test_renewal_is_refused_when_the_lease_changes_hands_during_it() -> None:
    async with live_redis() as redis:
        owner = LeaderLease(redis, KEYS.leader, ttl_s=30)
        await owner.hold()

        async def takeover() -> None:
            await redis.set(KEYS.leader, "someone-else", ex=999)

        racing = LeaderLease(InterferingRedis(redis, takeover), KEYS.leader, ttl_s=30)  # type: ignore[arg-type]
        racing._holder = owner._holder  # noqa: SLF001

        renewed = await racing.hold()

        assert (
            renewed,
            await redis.get(KEYS.leader),
            await redis.ttl(KEYS.leader) > 30,
        ) == (
            False,
            "someone-else",
            True,
        )


async def test_lock_is_released_only_by_its_holder() -> None:
    async with live_redis() as redis:
        coordinator = TokenCoordinator(redis, KEYS)
        token = await coordinator.acquire(CREDENTIAL)
        assert token is not None

        await coordinator.release(CREDENTIAL, "not-the-holder")
        still_locked = await coordinator.acquire(CREDENTIAL)
        await coordinator.release(CREDENTIAL, token)

        assert (still_locked, await coordinator.acquire(CREDENTIAL) is not None) == (
            None,
            True,
        )


async def test_lock_changed_by_someone_else_during_release_is_left_alone() -> None:
    async with live_redis() as redis:
        coordinator = TokenCoordinator(redis, KEYS)
        token = await coordinator.acquire(CREDENTIAL)
        assert token is not None

        async def takeover() -> None:
            await redis.set(KEYS.refresh_lock(CREDENTIAL), "new-owner", ex=60)

        racing = TokenCoordinator(InterferingRedis(redis, takeover), KEYS)  # type: ignore[arg-type]

        await racing.release(CREDENTIAL, token)

        assert await redis.get(KEYS.refresh_lock(CREDENTIAL)) == "new-owner"


def test_sync_lock_changed_during_release_is_left_alone() -> None:
    client = sync_redis_module.Redis.from_url(REDIS_URL, decode_responses=True)  # type: ignore[arg-type]
    client.flushdb()
    coordinator = SyncTokenCoordinator(client, KEYS, lock_wait_s=0.2)
    token = coordinator.acquire(CREDENTIAL)
    assert token is not None

    def takeover() -> None:
        client.set(KEYS.refresh_lock(CREDENTIAL), "new-owner", ex=60)

    SyncTokenCoordinator(SyncInterferingRedis(client, takeover), KEYS).release(  # type: ignore[arg-type]
        CREDENTIAL, token
    )

    assert client.get(KEYS.refresh_lock(CREDENTIAL)) == "new-owner"
    client.close()


def test_sync_lock_is_released_only_by_its_holder() -> None:
    client = sync_redis_module.Redis.from_url(REDIS_URL, decode_responses=True)  # type: ignore[arg-type]
    client.flushdb()
    coordinator = SyncTokenCoordinator(client, KEYS, lock_wait_s=0.2)
    token = coordinator.acquire(CREDENTIAL)
    assert token is not None

    coordinator.release(CREDENTIAL, "someone-else")
    held = coordinator.acquire(CREDENTIAL)
    coordinator.release(CREDENTIAL, token)

    assert (held, coordinator.acquire(CREDENTIAL) is not None) == (None, True)
    client.close()


async def test_slots_never_exceed_the_limit_under_concurrent_reservations() -> None:
    async with live_redis() as redis:
        slots = RedisSlotStore(redis, FakeClock(), KEYS.prefix)

        granted = await asyncio.gather(
            *(slots.reserve("a", f"t{index}", 10, 900.0) for index in range(60))
        )

        assert (granted.count(True), (await slots.in_flight(["a"]))["a"]) == (10, 10)


async def test_slot_of_a_dead_request_expires_with_its_score() -> None:
    async with live_redis() as redis:
        clock = FakeClock()
        slots = RedisSlotStore(redis, clock, KEYS.prefix)
        await slots.reserve("a", "t1", 1, 900.0)
        clock.advance(901)

        assert await slots.reserve("a", "t2", 1, 900.0)


async def test_change_notification_reaches_a_listener_on_another_connection() -> None:
    async with live_redis() as publisher, live_redis() as subscriber:
        received = asyncio.Event()
        task = asyncio.get_running_loop().create_task(
            RedisListener(subscriber, KEYS.changes, received.set).run()
        )
        try:
            await asyncio.sleep(0.2)
            await RedisNotifier(publisher, KEYS.changes).publish()
            await asyncio.wait_for(received.wait(), 2.0)
        finally:
            task.cancel()

        assert received.is_set()


class FailureLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


async def test_idle_listener_outlives_the_client_socket_timeout_without_failing() -> (
    None
):
    socket_timeout_s = 0.3
    failures = FailureLog()
    verbose_proxy_logger.addHandler(failures)
    subscriber = Redis.from_url(
        REDIS_URL, decode_responses=True, socket_timeout=socket_timeout_s  # type: ignore[arg-type]
    )
    received = asyncio.Event()
    task = asyncio.get_running_loop().create_task(
        RedisListener(subscriber, KEYS.changes, received.set).run()
    )
    try:
        await asyncio.sleep(socket_timeout_s * 6)
        async with live_redis() as publisher:
            await RedisNotifier(publisher, KEYS.changes).publish()
            await asyncio.wait_for(received.wait(), 2.0)
    finally:
        task.cancel()
        verbose_proxy_logger.removeHandler(failures)
        await subscriber.aclose()

    assert (failures.messages, received.is_set()) == ([], True)


async def test_state_write_notifies_other_replicas_through_real_pub_sub() -> None:
    async with live_redis() as writer, live_redis() as reader:
        store = RedisStateStore(
            writer,
            InMemoryStateDb(),
            FakeClock(),
            KEYS,
            RedisNotifier(writer, KEYS.changes),
        )
        received = asyncio.Event()
        task = asyncio.get_running_loop().create_task(
            RedisListener(reader, KEYS.changes, received.set).run()
        )
        try:
            await asyncio.sleep(0.2)
            await store.compare_and_set_state("a", None, record())
            await asyncio.wait_for(received.wait(), 2.0)
        finally:
            task.cancel()

        assert received.is_set()


async def test_owner_renews_its_lease_and_the_ttl_is_pushed_out() -> None:
    async with live_redis() as redis:
        lease = LeaderLease(redis, KEYS.leader, ttl_s=30)
        await lease.hold()
        await redis.expire(KEYS.leader, 5)

        renewed = await lease.hold()

        assert (renewed, await redis.ttl(KEYS.leader) > 5) == (True, True)


async def test_degraded_route_and_unsupported_model_marks_live_in_sorted_sets() -> None:
    from agentek_gateway.subscriptions.model import Route

    async with live_redis() as redis:
        clock = FakeClock()
        store = store_on(redis, clock)
        route = Route("chatgpt", "eu")
        await store.mark_route_degraded(route, 120.0)
        await store.mark_model_unsupported("a", "gpt-x", 60.0)

        seen = (await store.degraded_routes(), await store.unsupported_pairs())
        clock.advance(121)

        assert seen == (frozenset({route}), frozenset({("a", "gpt-x")}))
        assert (await store.degraded_routes(), await store.unsupported_pairs()) == (
            frozenset(),
            frozenset(),
        )


async def test_lease_key_lives_for_the_ttl_after_acquiring_and_after_each_renewal() -> (
    None
):
    async with live_redis() as redis:
        lease = LeaderLease(redis, KEYS.leader, ttl_s=30)

        await lease.hold()
        acquired_ttl = await redis.pttl(KEYS.leader)
        await redis.pexpire(KEYS.leader, 1_000)
        await lease.hold()
        renewed_ttl = await redis.pttl(KEYS.leader)

        assert 25_000 < acquired_ttl <= 30_000
        assert 25_000 < renewed_ttl <= 30_000


async def test_renewed_lease_still_expires_after_its_ttl_without_further_renewal() -> (
    None
):
    async with live_redis() as redis:
        lease = LeaderLease(redis, KEYS.leader, ttl_s=1)
        await lease.hold()
        await lease.hold()

        await asyncio.sleep(1.3)

        assert await redis.exists(KEYS.leader) == 0


async def test_extended_slot_survives_its_original_deadline_and_a_released_one_is_not_revived() -> (
    None
):
    async with live_redis() as redis:
        clock = FakeClock()
        slots = RedisSlotStore(redis, clock, KEYS.prefix)
        await slots.reserve("a", "long-stream", 1, 900.0)
        await slots.reserve("b", "gone", 1, 900.0)
        await slots.release("b", "gone")

        clock.advance(600)
        extended = await slots.extend("a", "long-stream", 900.0)
        revived = await slots.extend("b", "gone", 900.0)
        clock.advance(600)

        assert (extended, revived, (await slots.in_flight(["a", "b"]))) == (
            True,
            False,
            {"a": 1, "b": 0},
        )


async def test_refresh_mark_and_sticky_binding_expire_in_real_time() -> None:
    async with live_redis() as redis:
        store = store_on(redis, FakeClock())
        await store.mark_refreshed(CREDENTIAL, 1)
        await store.write_sticky("chat-1", "a", 1)
        before = (
            await store.recently_refreshed(CREDENTIAL),
            await store.read_sticky("chat-1"),
        )

        await asyncio.sleep(1.3)

        after = (
            await store.recently_refreshed(CREDENTIAL),
            await store.read_sticky("chat-1"),
        )
        assert (before, after) == ((True, "a"), (False, None))


async def test_usage_windows_round_trip_through_real_redis() -> None:
    async with live_redis() as redis:
        store = store_on(redis, FakeClock())
        usage = UsageRecord(
            Limits(Window(12.5, 2_000_000.0), Window(40.0, 3_000_000.0)), 1_000_000.0
        )

        await store.write_usage("a", usage)

        assert await store.read_all_usage() == {"a": usage}
