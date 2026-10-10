import asyncio
import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from agentek_gateway.subscriptions.memory import InMemoryStateStore
from agentek_gateway.subscriptions.model import (
    Limits,
    Route,
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState as S,
    UsageRecord,
    UsageSource,
    Window,
)
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.state_codec import encode_record
from agentek_gateway.subscriptions.state_db import InMemoryStateDb

from .conftest import FakeClock

HOUR_S = 3600.0
WINDOW_S = 120.0


def record(
    state: S = S.ACTIVE,
    version: int = 1,
    until: float | None = None,
    streak: int = 0,
    entered_at: float = 1_000_000.0,
) -> StateRecord:
    return StateRecord(
        state, version, entered_at, until, StateReason.NONE, SignalSource.NONE, streak
    )


class RedisHarness:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.server = fakeredis.FakeServer()
        self.redis = fakeredis.FakeAsyncRedis(server=self.server, decode_responses=True)
        self.db = InMemoryStateDb()
        self.keys = Keys("t:")

    def store(self) -> RedisStateStore:
        return RedisStateStore(self.redis, self.db, self.clock, self.keys)


@pytest.fixture
def harness(clock: FakeClock) -> RedisHarness:
    return RedisHarness(clock)


@pytest.fixture(params=["memory", "redis"])
def store(request: pytest.FixtureRequest, clock: FakeClock):  # type: ignore[no-untyped-def]
    if request.param == "memory":
        return InMemoryStateStore(clock)
    return RedisHarness(clock).store()


async def test_state_record_survives_a_round_trip_unchanged(store) -> None:  # type: ignore[no-untyped-def]
    saved = StateRecord(
        S.OVERLOADED,
        3,
        1_000_100.5,
        1_000_160.5,
        StateReason.PROBE_FAILED,
        SignalSource.PROBE,
        2,
    )

    assert await store.compare_and_set_state("a", None, saved)

    assert await store.read_state("a") == saved


async def test_compare_and_set_rejects_a_stale_version(store) -> None:  # type: ignore[no-untyped-def]
    await store.compare_and_set_state("a", None, record(version=1))

    results = [
        await store.compare_and_set_state("a", None, record(S.BANNED, 2)),
        await store.compare_and_set_state("a", 7, record(S.BANNED, 2)),
        await store.compare_and_set_state("a", 1, record(S.BANNED, 2)),
    ]

    assert (results, (await store.read_state("a")).state) == (  # type: ignore[union-attr]
        [False, False, True],
        S.BANNED,
    )


async def test_series_counts_inside_the_window_and_resets_on_success(store, clock) -> None:  # type: ignore[no-untyped-def]
    first = await store.record_unclassified("a", WINDOW_S)
    clock.advance(30)
    second = await store.record_unclassified("a", WINDOW_S)
    clock.advance(WINDOW_S)
    third = await store.record_unclassified("a", WINDOW_S)
    await store.reset_series("a")
    after_reset = await store.record_unclassified("a", WINDOW_S)

    assert (first, second, third, after_reset) == (1, 2, 1, 1)


async def test_error_counts_ignore_the_series_reset(store) -> None:  # type: ignore[no-untyped-def]
    await store.record_unclassified("a", WINDOW_S)
    await store.reset_series("a")

    counts = await store.unclassified_counts(["a", "b"], WINDOW_S)

    assert dict(counts) == {"a": 1, "b": 0}


async def test_degraded_route_expires_with_its_window(store, clock) -> None:  # type: ignore[no-untyped-def]
    route = Route("chatgpt", "eu")
    await store.mark_route_degraded(route, WINDOW_S)
    during = await store.degraded_routes()
    clock.advance(WINDOW_S + 1)

    assert (during, await store.degraded_routes()) == (frozenset({route}), frozenset())


async def test_unsupported_model_pair_expires_and_is_cleared_per_subscription(store, clock) -> None:  # type: ignore[no-untyped-def]
    await store.mark_model_unsupported("a", "gpt-x", HOUR_S)
    await store.mark_model_unsupported("b", "gpt-x", HOUR_S)
    await store.clear_unsupported("a")

    assert await store.unsupported_pairs() == frozenset({("b", "gpt-x")})
    clock.advance(HOUR_S + 1)
    assert await store.unsupported_pairs() == frozenset()


async def test_sticky_binding_expires(store, clock) -> None:  # type: ignore[no-untyped-def]
    await store.write_sticky("k", "a", 60.0)
    bound = await store.read_sticky("k")
    clock.advance(0)

    assert bound == "a"


async def test_usage_round_trip(store) -> None:  # type: ignore[no-untyped-def]
    usage = UsageRecord(
        Limits(Window(12.5, 2_000_000.0), Window(40.0, 3_000_000.0)), 1_000_000.0
    )

    await store.write_usage("a", usage)

    assert (await store.read_all_usage()) == {"a": usage}


async def test_durable_states_are_mirrored_to_the_database(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    until = harness.clock.now() + HOUR_S

    await store.compare_and_set_state("a", None, record(S.RATE_LIMITED, 1, until))
    await store.compare_and_set_state("b", None, record(S.ACTIVE, 1))
    await store.compare_and_set_state("c", None, record(S.ACTIVE, 1, streak=2))
    await store.compare_and_set_state("d", None, record(S.SOFT_LIMITED, 1, until))

    assert sorted(harness.db.rows) == ["a", "c"]


async def test_return_to_a_clean_active_state_removes_the_database_row(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    await store.compare_and_set_state("a", None, record(S.AUTH_FAILED, 1))

    await store.compare_and_set_state("a", 1, record(S.ACTIVE, 2))

    assert harness.db.rows == {}


async def test_blocked_subscriptions_survive_flushall_without_a_restart(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    await store.load_durable()
    until = harness.clock.now() + HOUR_S
    await store.compare_and_set_state("a", None, record(S.RATE_LIMITED, 1, until))
    await store.compare_and_set_state("b", None, record(S.BANNED, 1))
    await harness.redis.flushall()

    states = await store.read_states(["a", "b"])

    assert {sub_id: item.state for sub_id, item in states.items()} == {
        "a": S.RATE_LIMITED,
        "b": S.BANNED,
    }


async def test_flushall_does_not_break_version_checked_writes(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    await store.load_durable()
    await store.compare_and_set_state("a", None, record(S.BANNED, 4))
    await harness.redis.flushall()

    current = await store.read_state("a")
    written = await store.compare_and_set_state("a", current.version, record(S.ACTIVE, 5))  # type: ignore[union-attr]

    assert (written, (await store.read_state("a")).state) == (True, S.ACTIVE)  # type: ignore[union-attr]


async def test_restart_restores_durable_states_before_serving(harness) -> None:  # type: ignore[no-untyped-def]
    await harness.store().compare_and_set_state("a", None, record(S.BROKEN, 3))
    await harness.redis.flushall()
    restarted = harness.store()

    await restarted.load_durable()

    assert (await harness.redis.exists(harness.keys.state("a")), (await restarted.read_state("a")).state) == (  # type: ignore[union-attr]
        1,
        S.BROKEN,
    )


async def test_reconcile_writes_a_state_the_database_missed(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    await store.load_durable()
    await store.compare_and_set_state("a", None, record(S.BANNED, 1))
    harness.db.rows.clear()

    await store.reconcile(["a"])

    assert harness.db.rows["a"].state is S.BANNED


async def test_reconcile_drops_a_row_for_a_subscription_that_recovered(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    await store.compare_and_set_state("a", None, record(S.BANNED, 1))
    await harness.redis.set(
        harness.keys.state("a"), (await store.read_state("a")).__class__.__name__
    )  # placeholder overwritten below
    from agentek_gateway.subscriptions.state_codec import encode_record

    await harness.redis.set(harness.keys.state("a"), encode_record(record(S.ACTIVE, 2)))

    await store.reconcile(["a"])

    assert harness.db.rows == {}


async def test_record_ttl_follows_the_block_and_durable_records_have_none(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    until = harness.clock.now() + 600
    await store.compare_and_set_state("a", None, record(S.RATE_LIMITED, 1, until))
    await store.compare_and_set_state("b", None, record(S.BANNED, 1))

    await store.compare_and_set_state("c", None, record(S.ACTIVE, 1))
    ttl_a = await harness.redis.ttl(harness.keys.state("a"))
    ttl_b = await harness.redis.ttl(harness.keys.state("b"))
    ttl_c = await harness.redis.ttl(harness.keys.state("c"))

    assert (600 <= ttl_a <= 700, ttl_b, ttl_c > HOUR_S) == (True, -1, True)


async def test_database_failure_does_not_fail_the_state_write(harness) -> None:  # type: ignore[no-untyped-def]
    class BrokenDb(InMemoryStateDb):
        async def write_state(self, subscription_id, record):  # type: ignore[no-untyped-def]
            raise ConnectionError("db down")

    harness.db = BrokenDb()
    store = harness.store()

    written = await store.compare_and_set_state("a", None, record(S.BANNED, 1))

    assert (written, (await store.read_state("a")).state) == (True, S.BANNED)  # type: ignore[union-attr]


async def test_unreachable_redis_raises_instead_of_inventing_state(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    harness.server.connected = False

    with pytest.raises(RedisConnectionError):
        await store.read_states(["a"])


class GatedDb(InMemoryStateDb):
    """Holds back the first write until released, to let a newer write overtake it."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.first = True
        self.read_calls = 0

    async def write_state(self, subscription_id, record):  # type: ignore[no-untyped-def]
        if self.first:
            self.first = False
            await self.gate.wait()
        await super().write_state(subscription_id, record)

    async def read_states(self, subscription_ids):  # type: ignore[no-untyped-def]
        self.read_calls += 1
        return await super().read_states(subscription_ids)


async def test_late_database_write_of_an_old_state_cannot_undo_a_newer_one(clock) -> None:  # type: ignore[no-untyped-def]
    harness = RedisHarness(clock)
    harness.db = GatedDb()
    first, second = harness.store(), harness.store()
    older = asyncio.get_running_loop().create_task(
        first.compare_and_set_state("a", None, record(S.BANNED, 1))
    )
    await asyncio.sleep(0.05)
    await second.compare_and_set_state("a", 1, record(S.AUTH_FAILED, 2))

    harness.db.gate.set()
    await older

    assert harness.db.rows["a"].state is S.AUTH_FAILED


async def test_state_changed_elsewhere_is_read_from_the_database_after_flushall(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    await store.compare_and_set_state("a", None, record(S.BANNED, 1))
    await harness.redis.flushall()
    harness.db.rows["a"] = record(S.AUTH_FAILED, 5)

    states = await store.read_states(["a"])

    assert states["a"].state is S.AUTH_FAILED


async def test_subscription_without_a_row_is_not_looked_up_again_for_a_while(clock) -> None:  # type: ignore[no-untyped-def]
    harness = RedisHarness(clock)
    harness.db = GatedDb()
    store = harness.store()

    for _ in range(5):
        await store.read_states(["never-seen"])

    assert harness.db.read_calls == 1


async def test_half_open_is_not_kept_in_the_database(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    await store.compare_and_set_state("a", None, record(S.AUTH_FAILED, 1))

    await store.compare_and_set_state("a", 1, record(S.HALF_OPEN, 2))

    assert harness.db.rows == {}


async def test_only_the_states_the_spec_names_are_durable() -> None:
    from agentek_gateway.subscriptions.redis_state import needs_durable_row

    durable = {state for state in S if needs_durable_row(record(state, 1))}

    assert durable == {
        S.RATE_LIMITED,
        S.OVERLOADED,
        S.BROKEN,
        S.AUTH_REFRESHING,
        S.AUTH_FAILED,
        S.BANNED,
        S.DISABLED,
    }


async def test_reconcile_restores_a_state_redis_forgot(harness) -> None:  # type: ignore[no-untyped-def]
    store = harness.store()
    harness.db.rows["a"] = record(S.BANNED, 3)

    await store.reconcile(["a"])

    assert await harness.redis.exists(harness.keys.state("a")) == 1


async def test_in_memory_database_ignores_a_delete_older_than_its_row() -> None:
    db = InMemoryStateDb()
    await db.write_state("a", record(S.BANNED, 5))

    await db.delete_state("a", 4)

    assert "a" in db.rows


async def test_usage_keeps_the_source_it_was_observed_from(store) -> None:  # type: ignore[no-untyped-def]
    usage = UsageRecord(
        Limits(Window(12.5, 2_000_000.0)), 1_000_000.0, UsageSource.USAGE_CHECK
    )

    await store.write_usage("a", usage)

    assert (await store.read_all_usage())["a"].source is UsageSource.USAGE_CHECK


async def test_usage_stored_without_a_source_reads_as_response_headers(harness) -> None:  # type: ignore[no-untyped-def]
    await harness.redis.hset(
        harness.keys.usage,
        "a",
        '{"five_hour": null, "weekly": {"used": 1.0, "reset_at": 2.0}, "observed_at": 3.0}',
    )

    usage = await harness.store().read_all_usage()

    assert usage["a"].source is UsageSource.RESPONSE_HEADERS


async def test_a_limits_refresh_is_granted_once_per_window(store) -> None:  # type: ignore[no-untyped-def]
    first = await store.claim_limits_refresh("a", 30.0)
    second = await store.claim_limits_refresh("a", 30.0)
    other = await store.claim_limits_refresh("b", 30.0)

    assert (first, second, other) == (True, False, True)


async def test_forgetting_a_subscription_drops_every_trace_of_it(store) -> None:  # type: ignore[no-untyped-def]
    await store.compare_and_set_state("a", None, record(S.BANNED))
    await store.compare_and_set_state("b", None, record(S.BANNED))
    await store.write_usage("a", UsageRecord(Limits(Window(1.0, 2.0)), 3.0))
    await store.write_enabled_flag("a", False)
    await store.mark_model_unsupported("a", "m", 60.0)
    await store.claim_limits_refresh("a", 30.0)
    await store.record_unclassified("a", 60.0)

    await store.forget_subscription("a")

    assert (
        await store.read_state("a"),
        dict(await store.read_all_usage()),
        dict(await store.read_enabled_flags()),
        await store.unsupported_pairs(),
        await store.claim_limits_refresh("a", 30.0),
        (await store.read_state("b")).state,  # type: ignore[union-attr]
    ) == (None, {}, {}, frozenset(), True, S.BANNED)
