from agentek_gateway.subscriptions.events import (
    LimitExhausted,
    LimitWindow,
    OperatorDisabled,
    Overloaded,
)
from agentek_gateway.subscriptions.model import (
    Limits,
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState as S,
    Window,
    effective_state,
)
from agentek_gateway.subscriptions.providers.base import (
    AccountBanned,
    AuthRejected,
    ProbeResult,
    Unclassified,
)

from .upkeep import Replica, Upkeep, build_upkeep

HALF_OPEN_INTERVAL_S = 30.0
HALF_OPEN_JITTER_S = HALF_OPEN_INTERVAL_S / 2
BROKEN_INTERVAL_S = 30 * 60.0
PAST_ANY_OVERLOAD_PAUSE_S = 11 * 60.0
FAILED = ProbeResult(False, Unclassified(immediate=False, recognized=True), None)


async def current(replica: Replica, sub_id: str = "a") -> S | None:
    record = await replica.store.read_state(sub_id)
    return record.state if record else None


async def half_open_since_a_minute(
    upkeep: Upkeep, replica: Replica, *sub_ids: str
) -> None:
    for sub_id in sub_ids:
        subscription = next(
            item for item in await upkeep.repo.list_subscriptions() if item.id == sub_id
        )
        await replica.states.apply(subscription, Overloaded())
    upkeep.clock.advance(61)


async def test_probe_waits_for_its_jittered_time_then_returns_the_subscription() -> (
    None
):
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    await half_open_since_a_minute(upkeep, replica, "a")

    await replica.probes.tick()
    early = list(upkeep.provider.probes)
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)
    await replica.probes.tick()

    assert (early, len(upkeep.provider.probes), await current(replica)) == (
        [],
        1,
        S.ACTIVE,
    )


async def test_subscriptions_returning_together_are_probed_at_different_times() -> None:
    upkeep, _ = build_upkeep(["a", "b", "c"])
    delays = iter([2.0, 11.0, 25.0])
    replica = upkeep.replica(jitter=lambda limit_s: next(delays))
    await half_open_since_a_minute(upkeep, replica, "a", "b", "c")
    probe_seconds = []

    for second in range(30):
        before = len(upkeep.provider.probes)
        await replica.probes.tick()
        if len(upkeep.provider.probes) > before:
            probe_seconds.append(second)
        upkeep.clock.advance(1)

    assert probe_seconds == [2, 11, 25]


async def test_five_failed_probes_in_a_row_break_the_subscription() -> None:
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    upkeep.provider.health.extend([FAILED] * 4)
    await half_open_since_a_minute(upkeep, replica, "a")

    for _ in range(4):
        upkeep.clock.advance(PAST_ANY_OVERLOAD_PAUSE_S)
        await replica.probes.tick()
        upkeep.clock.advance(HALF_OPEN_INTERVAL_S)
        await replica.probes.tick()

    assert (await current(replica), len(upkeep.provider.probes)) == (S.BROKEN, 4)


async def test_broken_subscription_is_probed_rarely_and_returns_after_a_good_probe() -> (
    None
):
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    broken = StateRecord(
        S.BROKEN,
        1,
        upkeep.clock.now(),
        None,
        StateReason.UNHEALTHY,
        SignalSource.PROBE,
        5,
    )
    await replica.store.compare_and_set_state("a", None, broken)
    await replica.probes.tick()
    upkeep.clock.advance(BROKEN_INTERVAL_S - 60)
    await replica.probes.tick()
    before_interval = len(upkeep.provider.probes)

    upkeep.clock.advance(60 + HALF_OPEN_JITTER_S + 1)
    await replica.probes.tick()

    assert (before_interval, len(upkeep.provider.probes), await current(replica)) == (
        0,
        1,
        S.ACTIVE,
    )


async def test_probe_leaves_working_and_disabled_subscriptions_alone() -> None:
    upkeep, (_, disabled) = build_upkeep(["a", "b"])
    replica = upkeep.replica()
    await replica.states.apply(disabled, OperatorDisabled())

    for _ in range(3):
        await replica.probes.tick()
        upkeep.clock.advance(100)

    assert upkeep.provider.probes == []


async def test_exhausted_window_found_by_the_limits_check_keeps_the_subscription_blocked() -> (
    None
):
    upkeep, (subscription,) = build_upkeep(["a"])
    replica = upkeep.replica()
    await replica.states.apply(
        subscription, LimitExhausted(LimitWindow.WEEKLY, upkeep.clock.now() + 60)
    )
    upkeep.clock.advance(61)
    upkeep.provider.usage.append(
        Limits(weekly=Window(100.0, upkeep.clock.now() + 3600))
    )
    await replica.probes.tick()

    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)
    await replica.probes.tick()

    assert (await current(replica), upkeep.provider.probes) == (S.RATE_LIMITED, [])


async def test_probe_meeting_401_starts_a_refresh_and_a_deactivated_account_is_final() -> (
    None
):
    upkeep, _ = build_upkeep(["a", "b"])
    replica = upkeep.replica()
    upkeep.provider.health.extend(
        [
            ProbeResult(False, AuthRejected(), None),
            ProbeResult(False, AccountBanned(), None),
        ]
    )
    await half_open_since_a_minute(upkeep, replica, "a", "b")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)

    await replica.probes.tick()

    assert (await current(replica, "a"), await current(replica, "b")) == (
        S.AUTH_REFRESHING,
        S.BANNED,
    )


async def test_subscription_without_tokens_is_not_probed_and_not_penalized() -> None:
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    await half_open_since_a_minute(upkeep, replica, "a")
    del upkeep.credentials.values["cred-a"]
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)

    await replica.probes.tick()

    record = await replica.store.read_state("a")
    assert (
        upkeep.provider.probes,
        effective_state(record, upkeep.clock.now()),  # type: ignore[arg-type]
    ) == ([], S.HALF_OPEN)


async def test_probe_skips_a_subscription_switched_off_in_its_record() -> None:
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    await half_open_since_a_minute(upkeep, replica, "a")
    await upkeep.repo.set_enabled("a", False)

    for _ in range(3):
        await replica.probes.tick()
        upkeep.clock.advance(HALF_OPEN_INTERVAL_S)

    assert upkeep.provider.probes == []


async def test_good_probe_close_to_the_limit_returns_the_subscription_soft_limited() -> (
    None
):
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    upkeep.provider.health.append(
        ProbeResult(True, None, Limits(weekly=Window(96.0, upkeep.clock.now() + 7200)))
    )
    await half_open_since_a_minute(upkeep, replica, "a")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)

    await replica.probes.tick()

    assert await current(replica) == S.SOFT_LIMITED


async def test_limits_seen_by_the_usage_check_count_when_the_probe_returns_none() -> (
    None
):
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    upkeep.provider.usage.append(Limits(weekly=Window(97.0, upkeep.clock.now() + 7200)))
    await half_open_since_a_minute(upkeep, replica, "a")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)

    await replica.probes.tick()

    assert await current(replica) == S.SOFT_LIMITED


async def test_probe_pass_leaves_a_heartbeat_even_when_nothing_was_due() -> None:
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()

    await replica.probes.tick()

    assert await replica.store.read_probe_times() == {"chatgpt": upkeep.clock.now()}


async def test_lease_is_checked_before_each_probe_and_a_lost_lease_stops_the_pass() -> (
    None
):
    upkeep, _ = build_upkeep(["a", "b"])
    checks = []

    async def lose_after_first() -> bool:
        checks.append(1)
        return len(checks) == 1

    replica = upkeep.replica(still_leader=lose_after_first)
    await half_open_since_a_minute(upkeep, replica, "a", "b")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)

    await replica.probes.tick()

    assert (len(checks), len(upkeep.provider.probes)) == (2, 1)


async def test_subscription_that_recovers_and_breaks_again_is_probed_on_its_new_schedule() -> (
    None
):
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    await half_open_since_a_minute(upkeep, replica, "a")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)
    await replica.probes.tick()
    assert await current(replica) == S.ACTIVE
    await replica.probes.tick()
    probes_before = len(upkeep.provider.probes)

    await half_open_since_a_minute(upkeep, replica, "a")
    await replica.probes.tick()

    assert len(upkeep.provider.probes) == probes_before


async def test_broken_again_after_a_recovery_waits_the_full_broken_interval() -> None:
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    await half_open_since_a_minute(upkeep, replica, "a")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)
    await replica.probes.tick()
    await replica.probes.tick()
    probes_before = len(upkeep.provider.probes)
    current_record = await replica.store.read_state("a")
    broken = StateRecord(
        S.BROKEN,
        current_record.version + 1,  # type: ignore[union-attr]
        upkeep.clock.now(),
        None,
        StateReason.UNHEALTHY,
        SignalSource.PROBE,
        5,
    )
    await replica.store.compare_and_set_state("a", current_record.version, broken)  # type: ignore[union-attr]

    await replica.probes.tick()
    upkeep.clock.advance(BROKEN_INTERVAL_S - 60)
    await replica.probes.tick()

    assert len(upkeep.provider.probes) == probes_before


async def test_breaking_right_after_a_good_probe_does_not_inherit_the_old_schedule() -> (
    None
):
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    await half_open_since_a_minute(upkeep, replica, "a")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)
    await replica.probes.tick()
    probes_before = len(upkeep.provider.probes)
    record = await replica.store.read_state("a")
    broken = StateRecord(
        S.BROKEN,
        record.version + 1,
        upkeep.clock.now(),
        None,  # type: ignore[union-attr]
        StateReason.UNHEALTHY,
        SignalSource.PROBE,
        5,
    )
    await replica.store.compare_and_set_state("a", record.version, broken)  # type: ignore[union-attr]

    upkeep.clock.advance(HALF_OPEN_INTERVAL_S + 1)
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_INTERVAL_S + 1)
    await replica.probes.tick()

    assert len(upkeep.provider.probes) == probes_before


async def test_probes_that_die_on_the_transport_break_the_subscription_like_failed_ones() -> (
    None
):
    upkeep, _ = build_upkeep(["a"])
    replica = upkeep.replica()
    upkeep.provider.health_errors.extend([TimeoutError("egress down")] * 4)
    await half_open_since_a_minute(upkeep, replica, "a")

    for _ in range(4):
        upkeep.clock.advance(PAST_ANY_OVERLOAD_PAUSE_S)
        await replica.probes.tick()
        upkeep.clock.advance(HALF_OPEN_INTERVAL_S)
        await replica.probes.tick()

    assert (await current(replica), len(upkeep.provider.probes)) == (S.BROKEN, 4)


async def test_a_probe_that_raises_does_not_stop_the_other_subscriptions_being_probed() -> (
    None
):
    upkeep, _ = build_upkeep(["a", "b"])
    replica = upkeep.replica()
    upkeep.provider.health_errors.append(ConnectionError("reset"))
    await half_open_since_a_minute(upkeep, replica, "a", "b")
    await replica.probes.tick()
    upkeep.clock.advance(HALF_OPEN_JITTER_S + 1)

    await replica.probes.tick()

    assert (len(upkeep.provider.probes), await current(replica, "b")) == (2, S.ACTIVE)
