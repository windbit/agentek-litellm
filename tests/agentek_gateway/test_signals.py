import pytest

from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.model import Route, SubscriptionState as S
from agentek_gateway.subscriptions.signals import (
    Blocked,
    CommonCause,
    Recorded,
    common_cause_threshold,
)

from .conftest import Harness, make_subscription

ROUTE = Route("chatgpt", "eu")


async def state_of(harness: Harness, sub_id: str) -> S | None:
    record = await harness.store.read_state(sub_id)
    return record.state if record else None


@pytest.mark.parametrize(
    ("working", "threshold"),
    [(1, 2), (2, 2), (3, 2), (4, 2), (5, 3), (6, 3), (7, 4), (12, 6)],
)
def test_common_cause_threshold_is_half_of_the_working_with_a_floor_of_two(
    working: int, threshold: int
) -> None:
    assert common_cause_threshold(working) == threshold


async def test_single_error_does_not_block() -> None:
    harness = Harness([make_subscription("a")])

    outcome = await harness.signals.on_unclassified_error(
        harness.subscriptions["a"], immediate=False
    )

    assert (outcome, await state_of(harness, "a")) == (Recorded(1), None)


async def test_third_error_in_the_window_blocks_a_lone_subscription() -> None:
    harness = Harness([make_subscription("a")])
    sub = harness.subscriptions["a"]

    outcomes = [
        await harness.signals.on_unclassified_error(sub, immediate=False)
        for _ in range(3)
    ]

    assert (outcomes[-1], await state_of(harness, "a")) == (Blocked(), S.OVERLOADED)


async def test_errors_older_than_the_window_do_not_add_up() -> None:
    harness = Harness([make_subscription("a")])
    sub = harness.subscriptions["a"]
    await harness.signals.on_unclassified_error(sub, immediate=False)
    await harness.signals.on_unclassified_error(sub, immediate=False)
    harness.clock.advance(121)

    outcome = await harness.signals.on_unclassified_error(sub, immediate=False)

    assert (outcome, await state_of(harness, "a")) == (Recorded(1), None)


async def test_success_between_errors_resets_the_series() -> None:
    harness = Harness([make_subscription("a")])
    sub = harness.subscriptions["a"]
    await harness.signals.on_unclassified_error(sub, immediate=False)
    await harness.signals.on_unclassified_error(sub, immediate=False)
    await harness.signals.on_success(sub)

    outcome = await harness.signals.on_unclassified_error(sub, immediate=False)

    assert (outcome, await state_of(harness, "a")) == (Recorded(1), None)


async def test_overload_status_blocks_at_once() -> None:
    harness = Harness([make_subscription("a")])

    outcome = await harness.signals.on_unclassified_error(
        harness.subscriptions["a"], immediate=True
    )

    assert (outcome, await state_of(harness, "a")) == (Blocked(), S.OVERLOADED)


async def test_pause_doubles_for_a_repeated_overload_without_success() -> None:
    harness = Harness([make_subscription("a")])
    sub = harness.subscriptions["a"]
    await harness.signals.on_unclassified_error(sub, immediate=True)
    harness.clock.advance(61)

    await harness.signals.on_unclassified_error(sub, immediate=True)

    record = await harness.store.read_state("a")
    assert (record.overload_streak, record.until - harness.clock.now()) == (2, 120)  # type: ignore[union-attr]


async def test_four_subscriptions_of_one_route_overloaded_together_stay_working(
    harness_four: Harness,
) -> None:
    harness = harness_four
    for _ in range(3):
        for sub_id in ("s1", "s2", "s3", "s4"):
            await harness.signals.on_unclassified_error(
                harness.subscriptions[sub_id], immediate=False
            )

    states = [await state_of(harness, sub_id) for sub_id in ("s1", "s2", "s3", "s4")]
    assert (states, await harness.store.degraded_routes()) == (
        [None] * 4,
        frozenset({ROUTE}),
    )


async def test_common_cause_is_reported_with_the_counts(harness_four: Harness) -> None:
    harness = harness_four
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s2"], immediate=False
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s3"], immediate=False
    )

    outcome = await harness.signals.on_unclassified_error(
        harness.subscriptions["s1"], immediate=True
    )

    assert outcome == CommonCause(ROUTE, failed=3, working=4)


async def test_one_of_two_failing_while_the_other_succeeds_is_an_account_breakage() -> (
    None
):
    harness = Harness(
        [make_subscription("a", egress="eu"), make_subscription("b", egress="eu")]
    )
    await harness.signals.on_success(harness.subscriptions["b"])
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["a"], immediate=False
        )

    assert (await state_of(harness, "a"), await harness.store.degraded_routes()) == (
        S.OVERLOADED,
        frozenset(),
    )


async def test_both_of_two_failing_is_a_route_problem() -> None:
    harness = Harness(
        [make_subscription("a", egress="eu"), make_subscription("b", egress="eu")]
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["b"], immediate=False
    )
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["a"], immediate=False
        )

    assert (await state_of(harness, "a"), await harness.store.degraded_routes()) == (
        None,
        frozenset({ROUTE}),
    )


async def test_broken_account_among_healthy_neighbours_is_blocked() -> None:
    harness = Harness(
        [make_subscription(f"s{index}", egress="eu") for index in range(1, 5)]
    )
    for sub_id in ("s2", "s3", "s4"):
        await harness.signals.on_success(harness.subscriptions[sub_id])
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["s1"], immediate=False
        )

    assert (await state_of(harness, "s1"), await harness.store.degraded_routes()) == (
        S.OVERLOADED,
        frozenset(),
    )


async def test_a_single_subscription_never_triggers_the_common_cause_rule() -> None:
    harness = Harness([make_subscription("a", egress="eu")])
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["a"], immediate=False
        )

    assert await state_of(harness, "a") is S.OVERLOADED


async def test_failures_on_another_route_do_not_count() -> None:
    harness = Harness(
        [
            make_subscription("a", egress="eu"),
            make_subscription("b", egress="eu"),
            make_subscription("c", egress="us"),
        ]
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["c"], immediate=False
    )
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["a"], immediate=False
        )

    assert await state_of(harness, "a") is S.OVERLOADED


async def test_blocked_peers_that_failed_still_count_as_a_shared_cause() -> None:
    harness = Harness(
        [make_subscription(f"s{index}", egress="eu") for index in range(1, 5)]
    )
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["s1"], immediate=False
        )
    assert await state_of(harness, "s1") is S.OVERLOADED
    for sub_id in ("s2", "s3", "s4"):
        for _ in range(3):
            await harness.signals.on_unclassified_error(
                harness.subscriptions[sub_id], immediate=False
            )

    assert [await state_of(harness, sub_id) for sub_id in ("s2", "s3", "s4")] == [
        None,
        None,
        None,
    ]


async def test_configured_series_threshold_is_applied() -> None:
    config = GatewayConfig(defaults={"series_threshold": 2})  # type: ignore[arg-type]
    harness = Harness([make_subscription("a")], config)
    sub = harness.subscriptions["a"]

    await harness.signals.on_unclassified_error(sub, immediate=False)
    await harness.signals.on_unclassified_error(sub, immediate=False)

    assert await state_of(harness, "a") is S.OVERLOADED
