from collections.abc import Mapping, Sequence
from dataclasses import replace

from .clock import Clock
from .model import (
    EgressInfo,
    Route,
    StateRecord,
    Subscription,
    SubscriptionId,
    UsageRecord,
)
from .policy import Policy, SubscriptionPolicy


class InMemorySubscriptionRepo:
    def __init__(self, subscriptions: Sequence[Subscription] = ()) -> None:
        self._subscriptions = {
            subscription.id: subscription for subscription in subscriptions
        }

    async def list_subscriptions(self) -> Sequence[Subscription]:
        return tuple(self._subscriptions.values())

    async def set_enabled(self, subscription_id: SubscriptionId, enabled: bool) -> None:
        self._subscriptions[subscription_id] = replace(
            self._subscriptions[subscription_id], enabled=enabled
        )

    def put(self, subscription: Subscription) -> None:
        self._subscriptions[subscription.id] = subscription

    def remove(self, subscription_id: SubscriptionId) -> None:
        self._subscriptions.pop(subscription_id, None)


class InMemoryPolicyRepo:
    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or Policy()

    async def load_policy(self) -> Policy:
        return self.policy


class InMemoryPolicyBook:
    """Policy rows by subscription; reads like the policy repository and writes like the policy writer."""

    def __init__(self) -> None:
        self.rows: dict[SubscriptionId, SubscriptionPolicy] = {}
        self.loads = 0

    async def load_policy(self) -> Policy:
        self.loads += 1
        return Policy(
            visibility={sub_id: row.visibility for sub_id, row in self.rows.items()},
            bindings={
                sub_id: row.bound for sub_id, row in self.rows.items() if row.bound
            },
        )

    async def read(self, subscription_id: SubscriptionId) -> SubscriptionPolicy:
        return self.rows.get(subscription_id, SubscriptionPolicy())

    async def write(
        self, subscription_id: SubscriptionId, policy: SubscriptionPolicy
    ) -> None:
        self.rows[subscription_id] = policy


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
        self._flags: dict[SubscriptionId, bool] = {}
        self._refreshed: dict[str, float] = {}
        self._probed: dict[str, float] = {}
        self._limits_claims: dict[SubscriptionId, float] = {}
        self._egress: dict[Route, EgressInfo] = {}

    async def read_state(self, subscription_id: SubscriptionId) -> StateRecord | None:
        return self._states.get(subscription_id)

    async def read_states(
        self, subscription_ids: Sequence[SubscriptionId]
    ) -> Mapping[SubscriptionId, StateRecord]:
        return {
            sub_id: self._states[sub_id]
            for sub_id in subscription_ids
            if sub_id in self._states
        }

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

    async def write_enabled_flag(
        self, subscription_id: SubscriptionId, enabled: bool
    ) -> None:
        self._flags[subscription_id] = enabled

    async def read_enabled_flags(self) -> Mapping[SubscriptionId, bool]:
        return dict(self._flags)

    async def claim_limits_refresh(
        self, subscription_id: SubscriptionId, window_s: float
    ) -> bool:
        now = self._clock.now()
        if self._limits_claims.get(subscription_id, 0.0) > now:
            return False
        self._limits_claims[subscription_id] = now + window_s
        return True

    async def forget_subscription(self, subscription_id: SubscriptionId) -> None:
        self._states.pop(subscription_id, None)
        self._errors.pop(subscription_id, None)
        self._series.pop(subscription_id, None)
        self._usage.pop(subscription_id, None)
        self._flags.pop(subscription_id, None)
        self._limits_claims.pop(subscription_id, None)
        await self.clear_unsupported(subscription_id)

    async def mark_refreshed(self, credential_name: str, window_s: float) -> None:
        self._refreshed[credential_name] = self._clock.now() + window_s

    async def recently_refreshed(self, credential_name: str) -> bool:
        return self._refreshed.get(credential_name, 0.0) > self._clock.now()

    async def mark_probed(self, provider: str, at: float) -> None:
        self._probed[provider] = at

    async def read_probe_times(self) -> Mapping[str, float]:
        return dict(self._probed)

    async def write_egress(self, route: Route, info: EgressInfo) -> None:
        self._egress[route] = info

    async def read_egress(self) -> Mapping[Route, EgressInfo]:
        return dict(self._egress)
