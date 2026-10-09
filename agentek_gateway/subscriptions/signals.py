from dataclasses import dataclass
from math import ceil

from .clock import Clock
from .config import GatewayConfig
from .events import LimitsObserved, Overloaded, SeriesCleared, Succeeded
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
        """Records the error; on a shared cause lifts the series blocks of the route's subscriptions instead of blocking."""
        tuning = self._config.tuning_for(subscription.provider)
        count = await self._store.record_unclassified(
            subscription.id, tuning.series_window_s
        )
        route = Route(subscription.provider, subscription.egress)
        peers, failed, working = await self._route_picture(
            route, tuning.series_window_s
        )
        if failed >= common_cause_threshold(working):
            await self._store.mark_route_degraded(route, tuning.series_window_s)
            since = self._clock.now() - tuning.series_window_s
            for peer in peers:
                await self._states.apply(peer, SeriesCleared(since))
            return CommonCause(route, failed, working)
        if not immediate and count < tuning.series_threshold:
            return Recorded(count)
        await self._states.apply(subscription, Overloaded())
        return Blocked()

    async def on_success(
        self, subscription: Subscription, limits: Limits | None = None
    ) -> None:
        await self._store.reset_series(subscription.id)
        await self._states.apply(subscription, Succeeded())
        if limits and (limits.five_hour or limits.weekly):
            await self._states.apply(subscription, LimitsObserved(limits))

    async def _route_picture(
        self, route: Route, window_s: float
    ) -> tuple[list[Subscription], int, int]:
        now = self._clock.now()
        peers = [
            peer
            for peer in await self._repo.list_subscriptions()
            if peer.provider == route.provider
            and peer.egress == route.egress
            and peer.enabled
        ]
        states = await self._store.read_all_states()
        counts = await self._store.unclassified_counts(
            [peer.id for peer in peers], window_s
        )
        enabled = [
            peer
            for peer in peers
            if peer.id not in states
            or states[peer.id].state is not SubscriptionState.DISABLED
        ]
        failing = {peer.id for peer in enabled if counts.get(peer.id, 0) > 0}
        working = {
            peer.id
            for peer in enabled
            if peer.id in failing
            or is_working(
                effective_state(states[peer.id], now)
                if peer.id in states
                else SubscriptionState.ACTIVE
            )
        }
        return enabled, len(failing), len(working)
