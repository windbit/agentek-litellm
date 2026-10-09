from dataclasses import dataclass
from math import ceil

from .clock import Clock
from .config import GatewayConfig
from .events import LimitsObserved, Overloaded
from .model import (
    Limits,
    Route,
    Subscription,
    SubscriptionState,
    effective_state,
    is_working,
)
from .ports import StateStore, SubscriptionRepo
from .service import StateService

COMMON_CAUSE_MIN_FAILED = 2


@dataclass(frozen=True, slots=True)
class Recorded:
    count: int


@dataclass(frozen=True, slots=True)
class Blocked:
    pass


@dataclass(frozen=True, slots=True)
class CommonCause:
    route: Route
    failed: int
    working: int


UnclassifiedOutcome = Recorded | Blocked | CommonCause


def common_cause_threshold(working: int) -> int:
    return max(COMMON_CAUSE_MIN_FAILED, ceil(working / 2))


class SignalProcessor:
    def __init__(
        self,
        store: StateStore,
        repo: SubscriptionRepo,
        states: StateService,
        clock: Clock,
        config: GatewayConfig,
    ) -> None:
        self._store = store
        self._repo = repo
        self._states = states
        self._clock = clock
        self._config = config

    async def on_unclassified_error(
        self, subscription: Subscription, *, immediate: bool
    ) -> UnclassifiedOutcome:
        tuning = self._config.tuning_for(subscription.provider)
        count = await self._store.record_unclassified(
            subscription.id, tuning.series_window_s
        )
        if not immediate and count < tuning.series_threshold:
            return Recorded(count)
        route = Route(subscription.provider, subscription.egress)
        failed, working = await self._route_picture(route, tuning.series_window_s)
        if failed >= common_cause_threshold(working):
            await self._store.mark_route_degraded(route, tuning.series_window_s)
            return CommonCause(route, failed, working)
        await self._states.apply(subscription, Overloaded())
        return Blocked()

    async def on_success(
        self, subscription: Subscription, limits: Limits | None = None
    ) -> None:
        await self._store.clear_unclassified(subscription.id)
        await self._states.apply(subscription, LimitsObserved(limits or Limits()))

    async def _route_picture(self, route: Route, window_s: float) -> tuple[int, int]:
        now = self._clock.now()
        peers = [
            peer
            for peer in await self._repo.list_subscriptions()
            if peer.provider == route.provider and peer.egress == route.egress
        ]
        states = await self._store.read_all_states()
        counts = await self._store.unclassified_counts(
            [peer.id for peer in peers], window_s
        )
        failing = {peer.id for peer in peers if counts.get(peer.id, 0) > 0}
        working = {
            peer.id
            for peer in peers
            if peer.id in failing
            or is_working(
                effective_state(states[peer.id], now)
                if peer.id in states
                else SubscriptionState.ACTIVE
            )
        }
        return len(failing), len(working)
