from collections.abc import Mapping, Sequence
from typing import Protocol

from .model import Route, StateRecord, Subscription, SubscriptionId, UsageRecord
from .policy import Policy


class SubscriptionRepo(Protocol):
    async def list_subscriptions(self) -> Sequence[Subscription]: ...

    async def set_enabled(
        self, subscription_id: SubscriptionId, enabled: bool
    ) -> None: ...


class PolicyRepo(Protocol):
    async def load_policy(self) -> Policy: ...


class StateStore(Protocol):
    async def read_state(
        self, subscription_id: SubscriptionId
    ) -> StateRecord | None: ...

    async def read_all_states(self) -> Mapping[SubscriptionId, StateRecord]: ...

    async def compare_and_set_state(
        self,
        subscription_id: SubscriptionId,
        expected_version: int | None,
        record: StateRecord,
    ) -> bool: ...

    async def record_unclassified(
        self, subscription_id: SubscriptionId, window_s: float
    ) -> int: ...

    async def unclassified_counts(
        self, subscription_ids: Sequence[SubscriptionId], window_s: float
    ) -> Mapping[SubscriptionId, int]: ...

    async def reset_series(self, subscription_id: SubscriptionId) -> None: ...

    async def mark_route_degraded(self, route: Route, window_s: float) -> None: ...

    async def degraded_routes(self) -> frozenset[Route]: ...

    async def read_sticky(self, key: str) -> SubscriptionId | None: ...

    async def write_sticky(
        self, key: str, subscription_id: SubscriptionId, ttl_s: float
    ) -> None: ...

    async def mark_model_unsupported(
        self, subscription_id: SubscriptionId, model: str, ttl_s: float
    ) -> None: ...

    async def unsupported_pairs(self) -> frozenset[tuple[SubscriptionId, str]]: ...

    async def clear_unsupported(self, subscription_id: SubscriptionId) -> None: ...

    async def write_usage(
        self, subscription_id: SubscriptionId, usage: UsageRecord
    ) -> None: ...

    async def read_all_usage(self) -> Mapping[SubscriptionId, UsageRecord]: ...

    async def write_enabled_flag(
        self, subscription_id: SubscriptionId, enabled: bool
    ) -> None: ...

    async def read_enabled_flags(self) -> Mapping[SubscriptionId, bool]: ...

    async def mark_refreshed(self, credential_name: str, window_s: float) -> None: ...

    async def recently_refreshed(self, credential_name: str) -> bool: ...


class SlotStore(Protocol):
    async def reserve(
        self,
        subscription_id: SubscriptionId,
        token: str,
        limit: int | None,
        ttl_s: float,
    ) -> bool: ...

    async def release(self, subscription_id: SubscriptionId, token: str) -> bool: ...

    async def in_flight(
        self, subscription_ids: Sequence[SubscriptionId]
    ) -> Mapping[SubscriptionId, int]: ...


class StateDb(Protocol):
    async def read_state(
        self, subscription_id: SubscriptionId
    ) -> StateRecord | None: ...

    async def read_all_states(self) -> Mapping[SubscriptionId, StateRecord]: ...

    async def write_state(
        self, subscription_id: SubscriptionId, record: StateRecord
    ) -> None: ...

    async def delete_state(self, subscription_id: SubscriptionId) -> None: ...
