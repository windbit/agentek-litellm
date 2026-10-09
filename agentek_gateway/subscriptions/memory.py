from collections.abc import Mapping, Sequence

from .clock import Clock
from .model import Route, StateRecord, Subscription, SubscriptionId, UsageRecord
from .policy import Policy


class InMemorySubscriptionRepo:
    def __init__(self, subscriptions: Sequence[Subscription] = ()) -> None:
        self._subscriptions = {
            subscription.id: subscription for subscription in subscriptions
        }

    async def list_subscriptions(self) -> Sequence[Subscription]:
        return tuple(self._subscriptions.values())

    def put(self, subscription: Subscription) -> None:
        self._subscriptions[subscription.id] = subscription

    def remove(self, subscription_id: SubscriptionId) -> None:
        self._subscriptions.pop(subscription_id, None)


class InMemoryPolicyRepo:
    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or Policy()

    async def load_policy(self) -> Policy:
        return self.policy


class InMemoryStateStore:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._states: dict[SubscriptionId, StateRecord] = {}
        self._errors: dict[SubscriptionId, list[float]] = {}
        self._series: dict[SubscriptionId, list[float]] = {}
        self._degraded: dict[Route, float] = {}
        self._sticky: dict[str, tuple[SubscriptionId, float]] = {}
        self._unsupported: dict[tuple[SubscriptionId, str], float] = {}
        self._usage: dict[SubscriptionId, UsageRecord] = {}

    async def read_state(self, subscription_id: SubscriptionId) -> StateRecord | None:
        return self._states.get(subscription_id)

    async def read_all_states(self) -> Mapping[SubscriptionId, StateRecord]:
        return dict(self._states)

    async def compare_and_set_state(
        self,
        subscription_id: SubscriptionId,
        expected_version: int | None,
        record: StateRecord,
    ) -> bool:
        current = self._states.get(subscription_id)
        current_version = current.version if current else None
        if current_version != expected_version:
            return False
        self._states[subscription_id] = record
        return True

    async def record_unclassified(
        self, subscription_id: SubscriptionId, window_s: float
    ) -> int:
        now = self._clock.now()
        self._errors[subscription_id] = [
            stamp
            for stamp in self._errors.get(subscription_id, [])
            if now - stamp < window_s
        ] + [now]
        series = [
            stamp
            for stamp in self._series.get(subscription_id, [])
            if now - stamp < window_s
        ] + [now]
        self._series[subscription_id] = series
        return len(series)

    async def unclassified_counts(
        self, subscription_ids: Sequence[SubscriptionId], window_s: float
    ) -> Mapping[SubscriptionId, int]:
        now = self._clock.now()
        return {
            sub_id: sum(
                1 for stamp in self._errors.get(sub_id, []) if now - stamp < window_s
            )
            for sub_id in subscription_ids
        }

    async def reset_series(self, subscription_id: SubscriptionId) -> None:
        self._series.pop(subscription_id, None)

    async def mark_route_degraded(self, route: Route, window_s: float) -> None:
        self._degraded[route] = self._clock.now() + window_s

    async def degraded_routes(self) -> frozenset[Route]:
        now = self._clock.now()
        return frozenset(
            route for route, until in self._degraded.items() if until > now
        )

    async def read_sticky(self, key: str) -> SubscriptionId | None:
        entry = self._sticky.get(key)
        if entry is None or entry[1] <= self._clock.now():
            return None
        return entry[0]

    async def write_sticky(
        self, key: str, subscription_id: SubscriptionId, ttl_s: float
    ) -> None:
        self._sticky[key] = (subscription_id, self._clock.now() + ttl_s)

    async def mark_model_unsupported(
        self, subscription_id: SubscriptionId, model: str, ttl_s: float
    ) -> None:
        self._unsupported[(subscription_id, model)] = self._clock.now() + ttl_s

    async def unsupported_pairs(self) -> frozenset[tuple[SubscriptionId, str]]:
        now = self._clock.now()
        return frozenset(
            pair for pair, until in self._unsupported.items() if until > now
        )

    async def clear_unsupported(self, subscription_id: SubscriptionId) -> None:
        for pair in [pair for pair in self._unsupported if pair[0] == subscription_id]:
            del self._unsupported[pair]

    async def write_usage(
        self, subscription_id: SubscriptionId, usage: UsageRecord
    ) -> None:
        self._usage[subscription_id] = usage

    async def read_all_usage(self) -> Mapping[SubscriptionId, UsageRecord]:
        return dict(self._usage)
