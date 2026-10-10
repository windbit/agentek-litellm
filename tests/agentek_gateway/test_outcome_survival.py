"""An upstream failure must not be lost or ignored while Redis is slow or unreachable (acceptance defect D3)."""

import asyncio

from agentek_gateway.subscriptions.events import (
    LimitExhausted,
    LimitsObserved,
    LimitWindow,
    Succeeded,
)
from agentek_gateway.subscriptions.model import Limits, SubscriptionState as S, Window
from agentek_gateway.subscriptions.redis_state import RedisStateStore

from agentek_gateway.subscriptions.slots import ReserveRequest

from .plain import plain_runtime

HOUR_S = 3600.0
RESET_AT = 1_000_000.0 + 2 * HOUR_S


class UnreliableState(RedisStateStore):
    """Writes time out the given number of times; reads stay healthy."""

    write_failures = 0
    reads = 0
    usage_writes = 0
    series_resets = 0

    async def compare_and_set_state(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.write_failures:
            self.write_failures -= 1
            raise TimeoutError("Timeout reading from redis")
        return await super().compare_and_set_state(*args, **kwargs)

    async def read_state(self, subscription_id):  # type: ignore[no-untyped-def]
        self.reads += 1
        return await super().read_state(subscription_id)

    async def write_usage(self, subscription_id, usage):  # type: ignore[no-untyped-def]
        self.usage_writes += 1
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


async def test_success_resets_the_error_series_at_most_once_a_second() -> None:
    plain = plain_runtime(["a"], store_class=UnreliableState)
    subscription = (await plain.repo.list_subscriptions())[0]
    signals = plain.runtime.parts.signals

    for _ in range(5):
        await signals.on_success(subscription)
    plain.clock.advance(1.5)
    await signals.on_success(subscription)

    assert plain.store.series_resets == 2  # type: ignore[attr-defined]


async def test_success_after_an_error_of_this_process_resets_the_series_at_once() -> (
    None
):
    plain = plain_runtime(["a"], store_class=UnreliableState)
    subscription = (await plain.repo.list_subscriptions())[0]
    signals = plain.runtime.parts.signals
    await signals.on_success(subscription)

    await signals.on_unclassified_error(subscription, immediate=False)
    await signals.on_success(subscription)

    assert plain.store.series_resets == 2  # type: ignore[attr-defined]


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
