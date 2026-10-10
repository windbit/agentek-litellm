import asyncio
import time

import pytest

from agentek_gateway.subscriptions.memory import InMemorySubscriptionRepo
from agentek_gateway.subscriptions.plugin import _reconcile_forever
from agentek_gateway.subscriptions.model import SubscriptionState as S
from agentek_gateway.subscriptions.service import StateService
from agentek_gateway.subscriptions.toggle import SubscriptionToggle

from .conftest import make_subscription
from .plain import plain_runtime
from .stack import MODEL, Shared, Stack, account_of, running_stack

PROPAGATION_BUDGET_S = 1.0
LONG_INTERVAL_S = 30.0
SHORT_INTERVAL_S = 0.3


def visible_ids(stack: Stack) -> list[str]:
    snapshot = stack.runtime.parts.snapshot.current
    return sorted(
        sub_id for sub_id, sub in snapshot.subscriptions.items() if sub.enabled  # type: ignore[union-attr]
    )


async def offered(stack: Stack) -> list[str]:
    chosen = await stack.callback.async_filter_deployments(
        MODEL, stack.router.model_list, None, {"metadata": {}}
    )
    return [item["model_info"]["id"] for item in chosen]


async def wait_until(check, budget_s: float = 3.0) -> float:  # type: ignore[no-untyped-def]
    started = time.monotonic()
    while not check() and time.monotonic() - started < budget_s:
        await asyncio.sleep(0.005)
    return time.monotonic() - started


async def test_switching_off_blocks_the_subscription_and_switching_on_goes_through_a_probe() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        subscription = stack.subscriptions["a"]

        await stack.runtime.toggle.set_enabled(subscription, False)
        disabled = await stack.store.read_state("a")
        await stack.runtime.toggle.set_enabled(subscription, True)
        enabled = await stack.store.read_state("a")

        assert (disabled.state, enabled.state) == (S.DISABLED, S.HALF_OPEN)  # type: ignore[union-attr]


async def test_disabled_subscription_receives_no_request_and_its_deployments_stay() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        await stack.runtime.toggle.set_enabled(stack.subscriptions["a"], False)
        await stack.refresh()

        await stack.call()

        assert (stack.mock.accounts_served(), len(stack.router.model_list)) == (
            [account_of("b")],
            2,
        )


async def test_second_replica_stops_using_a_subscription_within_a_second_of_the_switch() -> (
    None
):
    shared = Shared()
    async with (
        running_stack(
            ["a", "b"], shared=shared, live=True, snapshot_interval_s=LONG_INTERVAL_S
        ) as first,
        running_stack(
            ["a", "b"], shared=shared, live=True, snapshot_interval_s=LONG_INTERVAL_S
        ) as second,
    ):
        await asyncio.sleep(0.1)
        assert await offered(second) == ["sub:a:gpt-5.4"]

        started = time.monotonic()
        await first.runtime.toggle.set_enabled(first.subscriptions["a"], False)
        elapsed = await wait_until(lambda: visible_ids(second) == ["b"])

        assert (visible_ids(second), elapsed < PROPAGATION_BUDGET_S) == (["b"], True)
        assert await offered(second) == ["sub:b:gpt-5.4"]
        assert time.monotonic() - started < PROPAGATION_BUDGET_S + 1


async def test_lost_notification_is_caught_by_the_snapshot_interval() -> None:
    shared = Shared()
    async with (
        running_stack(
            ["a", "b"], shared=shared, live=True, snapshot_interval_s=SHORT_INTERVAL_S
        ) as first,
        running_stack(
            ["a", "b"],
            shared=shared,
            live=True,
            listen=False,
            snapshot_interval_s=SHORT_INTERVAL_S,
        ) as second,
    ):
        await asyncio.sleep(0.1)

        await first.runtime.toggle.set_enabled(first.subscriptions["a"], False)
        elapsed = await wait_until(lambda: visible_ids(second) == ["b"])

        assert SHORT_INTERVAL_S * 0.0 <= elapsed <= SHORT_INTERVAL_S + 0.5
        assert visible_ids(second) == ["b"]


async def test_switch_off_survives_flushall() -> None:
    async with running_stack(["a", "b"]) as stack:
        await stack.runtime.toggle.set_enabled(stack.subscriptions["a"], False)
        await stack.redis.flushall()

        await stack.refresh()

        assert await offered(stack) == ["sub:b:gpt-5.4"]


async def test_flag_write_alone_notifies_other_replicas() -> None:
    shared = Shared()
    async with (
        running_stack(
            ["a", "b"], shared=shared, live=True, snapshot_interval_s=LONG_INTERVAL_S
        ) as first,
        running_stack(
            ["a", "b"], shared=shared, live=True, snapshot_interval_s=LONG_INTERVAL_S
        ) as second,
    ):
        await asyncio.sleep(0.1)

        await first.store.write_enabled_flag("a", False)
        elapsed = await wait_until(lambda: visible_ids(second) == ["b"])

        assert elapsed < PROPAGATION_BUDGET_S


async def test_switch_is_recorded_in_the_database_record_of_the_subscription() -> None:
    shared = Shared()
    async with running_stack(["a", "b"], shared=shared) as stack:
        await stack.runtime.toggle.set_enabled(stack.subscriptions["a"], False)

        listed = {sub.id: sub.enabled for sub in await shared.repo.list_subscriptions()}

        assert listed == {"a": False, "b": True}


async def test_database_value_applies_once_redis_forgot_the_flag_and_the_directory_reloaded() -> (
    None
):
    shared = Shared()
    async with running_stack(["a", "b"], shared=shared) as stack:
        await stack.runtime.toggle.set_enabled(stack.subscriptions["a"], False)
        await stack.redis.flushall()
        stack.clock.advance(31)

        await stack.refresh()

        assert visible_ids(stack) == ["b"]


class RefusingRepo(InMemorySubscriptionRepo):
    async def set_enabled(self, subscription_id: str, enabled: bool) -> None:
        raise ConnectionError("database down")


async def test_a_refused_database_write_leaves_no_flag_behind_to_outlive_a_flushall() -> (
    None
):
    plain = plain_runtime(["a"])
    subscription = make_subscription("a")
    toggle = SubscriptionToggle(
        plain.store,
        RefusingRepo([subscription]),
        StateService(plain.store, plain.clock, plain.runtime.parts.config),
    )

    with pytest.raises(ConnectionError):
        await toggle.set_enabled(subscription, False)

    assert (
        await plain.store.read_enabled_flags(),
        await plain.store.read_state("a"),
    ) == (
        {},
        None,
    )


async def test_a_flag_that_never_followed_the_database_is_rewritten_by_the_sweep() -> (
    None
):
    plain = plain_runtime(["a"])
    snapshot = plain.runtime.parts.snapshot
    await plain.store.write_enabled_flag("a", True)
    await plain.repo.set_enabled("a", False)
    await snapshot.refresh()
    before = snapshot.current.subscriptions["a"].enabled  # type: ignore[union-attr]

    await plain.store.reconcile_enabled_flags(await plain.repo.list_subscriptions())
    await snapshot.refresh()

    assert (before, snapshot.current.subscriptions["a"].enabled) == (True, False)  # type: ignore[union-attr]


async def test_the_background_sweep_brings_a_stale_flag_back_in_line_with_the_database() -> (
    None
):
    plain = plain_runtime(["a"])
    await plain.store.write_enabled_flag("a", True)
    await plain.repo.set_enabled("a", False)
    sweep = asyncio.get_running_loop().create_task(
        _reconcile_forever(plain.store, plain.repo, interval_s=0.02)
    )
    try:
        await wait_until(lambda: False, budget_s=0.3)
        flags = await plain.store.read_enabled_flags()
    finally:
        sweep.cancel()

    assert flags == {"a": False}
