"""An upstream failure must not be lost or ignored while Redis is slow or unreachable (acceptance defect D3)."""

import asyncio

import pytest

from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
from agentek_gateway.subscriptions.events import (
    Overloaded,
    LimitExhausted,
    LimitsObserved,
    LimitWindow,
    Succeeded,
)
from agentek_gateway.subscriptions.failures import FailedAttempt
from agentek_gateway.subscriptions.providers.base import AuthRejected, LimitReached
from agentek_gateway.subscriptions.model import Limits, SubscriptionState as S, Window
from agentek_gateway.subscriptions.redis_state import RedisStateStore

from agentek_gateway.subscriptions.slots import ReserveRequest

from .plain import plain_runtime
from .test_snapshot import UnreadableUsage

HOUR_S = 3600.0
RESET_AT = 1_000_000.0 + 2 * HOUR_S


class UnreliableState(RedisStateStore):
    """Writes time out the given number of times; reads stay healthy."""

    write_failures = 0
    reads = 0
    usage_writes = 0
    series_resets = 0
    usage_failures = 0
    refreshed_failures = 0
    flag_failures = 0

    async def compare_and_set_state(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.write_failures:
            self.write_failures -= 1
            raise TimeoutError("Timeout reading from redis")
        return await super().compare_and_set_state(*args, **kwargs)

    async def read_state(self, subscription_id):  # type: ignore[no-untyped-def]
        self.reads += 1
        return await super().read_state(subscription_id)

    async def recently_refreshed(self, credential_name):  # type: ignore[no-untyped-def]
        if self.refreshed_failures:
            raise TimeoutError("Timeout reading from redis")
        return await super().recently_refreshed(credential_name)

    async def read_enabled_flags(self):  # type: ignore[no-untyped-def]
        if self.flag_failures:
            raise TimeoutError("Timeout reading from redis")
        return await super().read_enabled_flags()

    async def write_usage(self, subscription_id, usage):  # type: ignore[no-untyped-def]
        self.usage_writes += 1
        if self.usage_failures:
            raise TimeoutError("Timeout reading from redis")
        await super().write_usage(subscription_id, usage)

    async def reset_series(self, subscription_id):  # type: ignore[no-untyped-def]
        self.series_resets += 1
        await super().reset_series(subscription_id)


class GatedReads(RedisStateStore):
    """The first read of the states after the gate is set up waits for it: a snapshot reload slowed by a busy event loop."""

    gate: asyncio.Event | None = None

    async def read_states(self, subscription_ids):  # type: ignore[no-untyped-def]
        stale = await super().read_states(subscription_ids)
        gate, self.gate = self.gate, None
        if gate is not None:
            await gate.wait()
        return stale


async def limited_sub_a(write_failures: int):  # type: ignore[no-untyped-def]
    plain = plain_runtime(["a", "b"], store_class=UnreliableState)
    await plain.runtime.parts.snapshot.refresh()
    plain.store.write_failures = write_failures  # type: ignore[attr-defined]
    subscription = (await plain.repo.list_subscriptions())[0]
    await plain.runtime.states.record(
        subscription, LimitExhausted(LimitWindow.WEEKLY, RESET_AT)
    )
    return plain, subscription


async def test_limit_is_in_force_here_while_redis_does_not_have_it_yet() -> None:
    plain, _ = await limited_sub_a(write_failures=10)

    assert (await plain.pick(), await plain.store.read_state("a")) == (
        ["sub:b:gpt-x"],
        None,
    )


async def test_snapshot_reload_does_not_bring_back_a_limit_redis_does_not_have_yet() -> (
    None
):
    plain, _ = await limited_sub_a(write_failures=10)

    await plain.runtime.parts.snapshot.refresh()

    assert await plain.pick() == ["sub:b:gpt-x"]


async def test_limit_reaches_redis_once_it_answers_again() -> None:
    plain, _ = await limited_sub_a(write_failures=2)

    await plain.runtime.writes.drain()

    stored = await plain.store.read_state("a")
    assert stored is not None and stored.state is S.RATE_LIMITED


async def test_event_that_changes_nothing_here_does_not_ask_redis() -> None:
    plain = plain_runtime(["a"], store_class=UnreliableState)
    await plain.runtime.parts.snapshot.refresh()
    subscription = (await plain.repo.list_subscriptions())[0]
    window = Window(10.0, RESET_AT)

    await plain.runtime.states.observe(subscription, Succeeded())
    await plain.runtime.states.observe(
        subscription, LimitsObserved(Limits(five_hour=window))
    )

    assert plain.store.reads == 0  # type: ignore[attr-defined]


async def test_event_that_changes_the_state_is_written_through() -> None:
    plain = plain_runtime(["a"], store_class=UnreliableState)
    await plain.runtime.parts.snapshot.refresh()
    subscription = (await plain.repo.list_subscriptions())[0]
    nearly_full = Limits(five_hour=Window(99.0, RESET_AT))

    await plain.runtime.states.observe(subscription, LimitsObserved(nearly_full))

    stored = await plain.store.read_state("a")
    assert stored is not None and stored.state is S.SOFT_LIMITED


async def test_repeated_window_readings_are_written_once_until_they_change() -> None:
    plain = plain_runtime(["a"], store_class=UnreliableState)
    failures = plain.runtime.parts.failures
    subscription = (await plain.repo.list_subscriptions())[0]
    provider = plain.runtime.parts.providers["chatgpt"]
    headers = {
        "x-codex-primary-used-percent": "12",
        "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-at": str(int(RESET_AT)),
    }
    changed = {**headers, "x-codex-primary-used-percent": "13"}

    for reading in (headers, headers, headers):
        await failures.record_usage(provider, subscription, reading)
    same_second = plain.store.usage_writes  # type: ignore[attr-defined]
    await failures.record_usage(provider, subscription, changed)
    plain.clock.advance(3)
    await failures.record_usage(provider, subscription, changed)
    plain.clock.advance(3)
    await failures.record_usage(
        provider, subscription, {**changed, "x-codex-primary-used-percent": "14"}
    )

    assert (same_second, plain.store.usage_writes) == (1, 3)  # type: ignore[attr-defined]


async def test_every_success_resets_the_shared_error_series() -> None:
    plain = plain_runtime(["a"], store_class=UnreliableState)
    subscription = (await plain.repo.list_subscriptions())[0]
    signals = plain.runtime.parts.signals

    for _ in range(3):
        await signals.on_success(subscription)

    assert plain.store.series_resets == 3  # type: ignore[attr-defined]


async def test_reload_that_read_redis_before_our_write_does_not_bring_the_limit_back() -> (
    None
):
    plain = plain_runtime(["a", "b"], store_class=GatedReads)
    await plain.runtime.parts.snapshot.refresh()
    gate = asyncio.Event()
    plain.store.gate = gate  # type: ignore[attr-defined]
    reload = asyncio.get_running_loop().create_task(
        plain.runtime.parts.snapshot.refresh()
    )
    await asyncio.sleep(0.05)
    subscription = (await plain.repo.list_subscriptions())[0]

    await plain.runtime.states.record(
        subscription, LimitExhausted(LimitWindow.WEEKLY, RESET_AT)
    )
    gate.set()
    await reload

    assert await plain.pick() == ["sub:b:gpt-x"]


async def test_request_started_here_counts_as_load_before_the_snapshot_is_reloaded() -> (
    None
):
    plain = plain_runtime(["a", "b"])
    await plain.runtime.parts.snapshot.refresh()
    subscription = (await plain.repo.list_subscriptions())[0]
    ledger = plain.runtime.parts.ledger

    first = await plain.pick("r1")
    reservation = await ledger.reserve(
        ReserveRequest("r1", "sub:a:gpt-x", subscription, "gpt-x", 1, None)
    )
    second = await plain.pick("r2")
    await ledger.release(reservation)  # type: ignore[arg-type]
    third = await plain.pick("r3")

    assert (first, second, third) == (["sub:a:gpt-x"], ["sub:b:gpt-x"], ["sub:a:gpt-x"])


LIMIT_HEADERS = {
    "x-codex-primary-used-percent": "100",
    "x-codex-primary-window-minutes": "300",
    "x-codex-primary-reset-at": str(int(RESET_AT)),
}


async def failed_attempt(plain, status: int = 429):  # type: ignore[no-untyped-def]
    subscription = (await plain.repo.list_subscriptions())[0]
    reservation = await plain.runtime.parts.ledger.reserve(
        ReserveRequest("r1", "sub:a:gpt-x", subscription, "gpt-x", 1, None)
    )
    return FailedAttempt(subscription, reservation, status, LIMIT_HEADERS, "")  # type: ignore[arg-type]


async def test_limit_is_queued_even_when_the_usage_windows_cannot_be_written() -> None:
    plain = plain_runtime(["a", "b"], store_class=UnreliableState)
    await plain.runtime.parts.snapshot.refresh()
    plain.store.usage_failures = 1  # type: ignore[attr-defined]
    plain.store.write_failures = 1  # type: ignore[attr-defined]
    attempt = await failed_attempt(plain)
    provider = plain.runtime.parts.providers["chatgpt"]

    await plain.runtime.parts.failures.handle(
        provider, attempt, LimitReached(LimitWindow.FIVE_HOUR, RESET_AT)
    )
    await plain.runtime.writes.drain()

    stored = await plain.store.read_state("a")
    assert stored is not None and stored.state is S.RATE_LIMITED


async def test_unauthorized_is_recorded_when_redis_cannot_say_whether_a_refresh_just_ran() -> (
    None
):
    plain = plain_runtime(["a", "b"], store_class=UnreliableState)
    await plain.runtime.parts.snapshot.refresh()
    plain.store.refreshed_failures = 1  # type: ignore[attr-defined]
    attempt = await failed_attempt(plain, 401)
    provider = plain.runtime.parts.providers["chatgpt"]

    await plain.runtime.parts.failures.handle(provider, attempt, AuthRejected())

    stored = await plain.store.read_state("a")
    assert stored is not None and stored.state is S.AUTH_REFRESHING


async def test_deferred_limits_of_one_subscription_leave_the_longer_block_standing() -> (
    None
):
    plain = plain_runtime(["a", "b"], store_class=UnreliableState)
    await plain.runtime.parts.snapshot.refresh()
    subscription = (await plain.repo.list_subscriptions())[0]
    plain.store.write_failures = 3  # type: ignore[attr-defined]
    longer = LimitExhausted(LimitWindow.WEEKLY, 1_000_000.0 + 5 * HOUR_S)
    shorter = LimitExhausted(LimitWindow.WEEKLY, 1_000_000.0 + HOUR_S)

    await plain.runtime.states.record(subscription, longer)
    await plain.runtime.states.record(subscription, shorter)
    await plain.runtime.writes.drain()

    stored = await plain.store.read_state("a")
    assert stored is not None and stored.until == 1_000_000.0 + 5 * HOUR_S


async def test_picture_with_a_change_notification_waiting_is_not_trusted_to_skip_redis() -> (
    None
):
    plain = plain_runtime(["a"], store_class=UnreliableState)
    await plain.runtime.parts.snapshot.refresh()
    subscription = (await plain.repo.list_subscriptions())[0]
    overloaded_elsewhere = await plain.runtime.states.apply(subscription, Overloaded())
    del overloaded_elsewhere
    plain.runtime.parts.snapshot.request_refresh()

    await plain.runtime.states.observe(subscription, Succeeded())

    assert plain.store.reads > 0  # type: ignore[attr-defined]


async def test_blocked_state_survives_a_flush_after_redis_had_caught_up() -> None:
    plain, _ = await limited_sub_a(write_failures=0)
    await plain.runtime.parts.snapshot.refresh()
    await plain.redis.flushall()

    await plain.runtime.parts.snapshot.refresh()

    assert await plain.pick() == ["sub:b:gpt-x"]


async def test_enabled_flags_that_cannot_be_read_fall_back_to_the_database_row() -> (
    None
):
    plain = plain_runtime(["a"], store_class=UnreliableState)
    snapshot = plain.runtime.parts.snapshot
    await plain.store.write_enabled_flag("a", False)
    await snapshot.refresh()
    plain.store.flag_failures = 1  # type: ignore[attr-defined]

    await snapshot.refresh()

    assert snapshot.current is not None and snapshot.current.subscriptions["a"].enabled


async def test_started_here_adds_to_the_reloaded_count_instead_of_hiding_behind_it() -> (
    None
):
    plain = plain_runtime(["a", "b"])
    await plain.runtime.parts.snapshot.refresh()
    subscriptions = await plain.repo.list_subscriptions()
    ledger = plain.runtime.parts.ledger
    await ledger.reserve(
        ReserveRequest("r1", "sub:a:gpt-x", subscriptions[0], "gpt-x", 1, None)
    )
    await plain.runtime.parts.snapshot.refresh()
    await ledger.reserve(
        ReserveRequest("r2", "sub:b:gpt-x", subscriptions[1], "gpt-x", 1, None)
    )

    assert await plain.pick("r3") == ["sub:a:gpt-x"]


async def test_concurrent_readings_of_one_subscription_are_written_once() -> None:
    plain = plain_runtime(["a"], store_class=UnreliableState)
    failures = plain.runtime.parts.failures
    subscription = (await plain.repo.list_subscriptions())[0]
    provider = plain.runtime.parts.providers["chatgpt"]

    await asyncio.gather(
        *(
            failures.record_usage(provider, subscription, LIMIT_HEADERS)
            for _ in range(5)
        )
    )

    assert plain.store.usage_writes == 1  # type: ignore[attr-defined]


async def test_failed_usage_write_is_retried_by_the_next_reading_and_never_raised() -> (
    None
):
    plain = plain_runtime(["a"], store_class=UnreliableState)
    failures = plain.runtime.parts.failures
    subscription = (await plain.repo.list_subscriptions())[0]
    provider = plain.runtime.parts.providers["chatgpt"]
    plain.store.usage_failures = 1  # type: ignore[attr-defined]

    await failures.record_usage(provider, subscription, LIMIT_HEADERS)
    plain.store.usage_failures = 0  # type: ignore[attr-defined]
    await failures.record_usage(provider, subscription, LIMIT_HEADERS)

    assert plain.store.usage_writes == 2  # type: ignore[attr-defined]


async def test_retry_settings_follow_the_pool_and_bound_the_clients_count() -> None:
    sub_ids = [f"s{index:02d}" for index in range(40)]
    plain = plain_runtime(sub_ids)
    await plain.runtime.parts.snapshot.refresh()
    gateway = plain.runtime.gateway

    settings = gateway.retry_settings({"model": "gpt-x", "num_retries": 100})
    other = gateway.retry_settings({"model": "some-other-model"})
    own_policy = gateway.retry_settings(
        {
            "model": "gpt-x",
            "model_group_retry_policy": {"gpt-x": {"TimeoutErrorRetries": 1}},
        }
    )

    assert (
        settings["num_retries"],
        settings["model_group_retry_policy"],
        other,
        own_policy["model_group_retry_policy"],
    ) == (
        32,
        {"gpt-x": {"RateLimitErrorRetries": 32}},
        {},
        {"gpt-x": {"TimeoutErrorRetries": 1, "RateLimitErrorRetries": 32}},
    )


async def test_small_pool_still_gets_the_default_retry_floor() -> None:
    plain = plain_runtime(["a", "b"])
    await plain.runtime.parts.snapshot.refresh()

    settings = plain.runtime.gateway.retry_settings({"model": "gpt-x"})

    assert settings["model_group_retry_policy"] == {
        "gpt-x": {"RateLimitErrorRetries": 4}
    }


async def test_request_that_has_retried_past_its_time_budget_ends_with_the_plugin_429() -> (
    None
):
    plain = plain_runtime(["a", "b"])
    await plain.runtime.parts.snapshot.refresh()
    request = {"metadata": {"agentek_request_id": "r1"}}
    plain.runtime.parts.attempts.record("r1", "sub:a:gpt-x", 1)
    plain.clock.advance(61)

    with pytest.raises(NoAvailableSubscriptionsError):
        await plain.runtime.gateway.filter("gpt-x", plain.deployments, request)
