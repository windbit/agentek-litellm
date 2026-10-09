import asyncio

import pytest

from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.events import (
    AccountDeactivated,
    Event,
    LimitExhausted,
    LimitWindow,
    OperatorDisabled,
    OperatorEnabled,
    Overloaded,
    TokenRevoked,
    Unauthorized,
)
from agentek_gateway.subscriptions.memory import InMemoryStateStore
from agentek_gateway.subscriptions.model import Subscription, SubscriptionState as S
from agentek_gateway.subscriptions.service import (
    MAX_CAS_ATTEMPTS,
    StateConflictError,
    StateService,
)

from .conftest import FakeClock, make_subscription


class InterleavingStore(InMemoryStateStore):
    """Both writers read the same version before either writes: the second CAS must fail and retry."""

    def __init__(self, clock: FakeClock, writers: int) -> None:
        super().__init__(clock)
        self._barrier = asyncio.Barrier(writers)
        self.reads = 0

    async def read_state(self, subscription_id: str):  # type: ignore[no-untyped-def]
        record = await super().read_state(subscription_id)
        self.reads += 1
        if self.reads <= self._barrier.parties:
            await self._barrier.wait()
        return record


class HostileStore(InMemoryStateStore):
    async def compare_and_set_state(self, subscription_id, expected_version, record):  # type: ignore[no-untyped-def]
        return False


def service(store: InMemoryStateStore, clock: FakeClock) -> StateService:
    return StateService(store, clock, GatewayConfig())


@pytest.mark.parametrize(
    ("first", "second", "survivor"),
    [
        (TokenRevoked(), Overloaded(), S.AUTH_FAILED),
        (Overloaded(), TokenRevoked(), S.AUTH_FAILED),
        (AccountDeactivated(), TokenRevoked(), S.BANNED),
        (TokenRevoked(), AccountDeactivated(), S.BANNED),
        (LimitExhausted(LimitWindow.WEEKLY, 1_003_600.0), Overloaded(), S.RATE_LIMITED),
        (Overloaded(), LimitExhausted(LimitWindow.WEEKLY, 1_003_600.0), S.RATE_LIMITED),
        (Unauthorized(), Overloaded(), S.AUTH_REFRESHING),
    ],
)
async def test_two_replicas_racing_keep_the_heavier_state(
    first: Event, second: Event, survivor: S
) -> None:
    clock = FakeClock()
    store = InterleavingStore(clock, writers=2)
    sub = make_subscription("a")

    await asyncio.gather(
        service(store, clock).apply(sub, first),
        service(store, clock).apply(sub, second),
    )

    assert (await store.read_state("a")).state is survivor  # type: ignore[union-attr]


async def test_losing_writer_retries_instead_of_overwriting() -> None:
    clock = FakeClock()
    store = InterleavingStore(clock, writers=2)
    sub = make_subscription("a")

    await asyncio.gather(
        service(store, clock).apply(sub, Overloaded()),
        service(store, clock).apply(sub, TokenRevoked()),
    )

    assert store.reads > 2


async def test_unchanged_event_does_not_write() -> None:
    clock = FakeClock()
    store = InMemoryStateStore(clock)
    sub = make_subscription("a")

    result = await service(store, clock).apply(sub, OperatorEnabled())

    assert (result.changed, await store.read_state("a")) == (False, None)


async def test_elapsed_deadline_is_applied_before_the_event() -> None:
    clock = FakeClock()
    store = InMemoryStateStore(clock)
    svc = service(store, clock)
    sub = make_subscription("a")
    await svc.apply(sub, Overloaded())
    clock.advance(61)

    result = await svc.apply(sub, Overloaded())

    assert (result.record.state, result.record.overload_streak) == (S.OVERLOADED, 2)


async def test_expired_state_is_persisted_even_when_the_event_changes_nothing() -> None:
    clock = FakeClock()
    store = InMemoryStateStore(clock)
    svc = service(store, clock)
    sub = make_subscription("a")
    await svc.apply(sub, Overloaded())
    clock.advance(61)

    await svc.apply(sub, OperatorEnabled())

    assert (await store.read_state("a")).state is S.HALF_OPEN  # type: ignore[union-attr]


async def test_disable_then_enable_goes_through_a_probe() -> None:
    clock = FakeClock()
    store = InMemoryStateStore(clock)
    svc = service(store, clock)
    sub = make_subscription("a")

    await svc.apply(sub, OperatorDisabled())
    result = await svc.apply(sub, OperatorEnabled())

    assert result.record.state is S.HALF_OPEN


async def test_endless_conflicts_fail_loudly() -> None:
    clock = FakeClock()
    sub: Subscription = make_subscription("a")

    with pytest.raises(StateConflictError, match=str(MAX_CAS_ATTEMPTS)):
        await service(HostileStore(clock), clock).apply(sub, Overloaded())
