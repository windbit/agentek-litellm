import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TypeVar

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .model import StateRecord, Subscription, SubscriptionId, initial_record
from .ports import PolicyRepo, SlotStore, StateStore, SubscriptionRepo
from .provider_settings import ProviderSettingsRepo
from .selection import Snapshot, subscription_of

T = TypeVar("T")

Deployment = Mapping[str, object]
ModelList = Callable[[], Sequence[Deployment]]

EMPTY_MODEL_LIST: ModelList = tuple


@dataclass(frozen=True, slots=True)
class SnapshotSources:
    repo: SubscriptionRepo
    policy: PolicyRepo
    store: StateStore
    slots: SlotStore
    model_list: ModelList = EMPTY_MODEL_LIST
    local_in_flight: Callable[[], Mapping[SubscriptionId, int]] = dict
    provider_settings: ProviderSettingsRepo | None = None


@dataclass(frozen=True, slots=True)
class KnownState:
    """A state learned in this process; durable once Redis has it, kept a while longer for a reload that read Redis before that."""

    record: StateRecord
    at: float
    durable: bool


@dataclass(frozen=True, slots=True)
class SnapshotTiming:
    interval_s: float = 1.0
    max_stale_s: float = 60.0
    directory_ttl_s: float = 5.0
    held_ttl_s: float = 600.0
    settled_grace_s: float = 30.0


DEFAULT_TIMING = SnapshotTiming()


def subscription_models(
    deployments: Sequence[Deployment], snapshot: Snapshot
) -> frozenset[str]:
    """Model groups served by at least one subscription deployment."""
    names: set[str] = set()
    for deployment in deployments:
        model_info = deployment.get("model_info")
        deployment_id = (
            str(model_info.get("id", "")) if isinstance(model_info, Mapping) else ""
        )
        params = deployment.get("litellm_params")
        if subscription_of(deployment_id, params, snapshot):
            names.add(str(deployment.get("model_name")))
    return frozenset(names)


class SnapshotCache:
    """In-memory view the filter reads; closed until the first load and again when it goes stale."""

    def __init__(
        self,
        sources: SnapshotSources,
        clock: Clock,
        timing: SnapshotTiming = DEFAULT_TIMING,
    ) -> None:
        self._sources = sources
        self._clock = clock
        self._timing = timing
        self._snapshot: Snapshot | None = None
        self._closed_variant: tuple[Snapshot, Snapshot] | None = None
        self._loaded_at: float | None = None
        self._directory: tuple[float, Sequence[Subscription]] | None = None
        self._known: dict[SubscriptionId, KnownState] = {}
        self._part_read_at: dict[str, float] = {}
        self._wake = asyncio.Event()

    @property
    def current(self) -> Snapshot | None:
        """None before the first load; a closed copy once the data is older than max_stale_s."""
        snapshot = self._snapshot
        if snapshot is None or self._loaded_at is None:
            return None
        if self._clock.now() - self._loaded_at <= self._timing.max_stale_s:
            return snapshot
        if self._closed_variant is None or self._closed_variant[0] is not snapshot:
            self._closed_variant = (snapshot, replace(snapshot, closed=True))
        return self._closed_variant[1]

    def request_refresh(self) -> None:
        self._directory = None
        self._wake.set()

    def is_current(self) -> bool:
        """True while the picture was reloaded within two intervals and no change notification is waiting to be read."""
        if self._loaded_at is None or self._wake.is_set():
            return False
        return self._clock.now() - self._loaded_at <= 2 * self._timing.interval_s

    def local_record(self, subscription_id: SubscriptionId) -> StateRecord | None:
        """None before the first load; a subscription without a stored state reads as a fresh ACTIVE one."""
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.states.get(subscription_id) or initial_record(self._clock.now())

    def hold(self, subscription_id: SubscriptionId, record: StateRecord) -> None:
        """A state this process knows and Redis may not yet: visible to the filter now, and kept over reloads until Redis catches up."""
        self._known[subscription_id] = KnownState(record, self._clock.now(), False)
        self._patch_state(subscription_id, record)

    def settle(self, subscription_id: SubscriptionId, record: StateRecord) -> None:
        known = self._known.get(subscription_id)
        if known is not None and known.record.version <= record.version:
            self._known[subscription_id] = KnownState(record, self._clock.now(), True)
        self._patch_state(subscription_id, record)

    def _patch_state(
        self, subscription_id: SubscriptionId, record: StateRecord
    ) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        known = snapshot.states.get(subscription_id)
        if known is not None and known.version > record.version:
            return
        self._snapshot = replace(
            snapshot, states={**snapshot.states, subscription_id: record}
        )

    def _with_known(
        self, states: Mapping[SubscriptionId, StateRecord]
    ) -> Mapping[SubscriptionId, StateRecord]:
        """Redis wins while its state is newer than ours; a state ours is as new as keeps standing in for a Redis that lost it."""
        now = self._clock.now()
        merged = dict(states)
        kept = {}
        for sub_id, known in self._known.items():
            ttl_s = (
                self._timing.settled_grace_s
                if known.durable
                else self._timing.held_ttl_s
            )
            stored = merged.get(sub_id)
            if now - known.at >= ttl_s or (
                stored is not None and stored.version > known.record.version
            ):
                continue
            kept[sub_id] = known
            if stored is None or stored.version < known.record.version:
                merged[sub_id] = known.record
        self._known = kept
        return merged

    async def refresh(self) -> Snapshot:
        sources = self._sources
        directory = await self._subscriptions()
        ids = [subscription.id for subscription in directory]
        previous = self._snapshot
        started_here = dict(sources.local_in_flight())
        states, *optional = await asyncio.gather(
            sources.store.read_states(ids),
            sources.store.read_all_usage(),
            sources.store.unsupported_pairs(),
            sources.store.read_enabled_flags(),
            sources.slots.in_flight(ids),
            return_exceptions=True,
        )
        if isinstance(states, BaseException):
            raise states
        usage = self._kept_on_failure(
            "usage windows", optional[0], previous.usage if previous else {}
        )
        unsupported = self._kept_on_failure(
            "unsupported models",
            optional[1],
            previous.unsupported if previous else frozenset(),
        )
        flags = self._flags_or_database(optional[2])
        in_flight = self._kept_on_failure(
            "in-flight counts", optional[3], previous.in_flight if previous else {}
        )
        provider_limits = await self._provider_limits()
        subscriptions = [
            replace(
                subscription,
                enabled=flags.get(subscription.id, subscription.enabled),
                concurrency_limit=subscription.concurrency_limit
                or provider_limits.get(subscription.provider),
            )
            for subscription in directory
        ]
        policy = await sources.policy.load_policy()
        draft = Snapshot(
            subscriptions={sub.id: sub for sub in subscriptions},
            states=self._with_known(states),
            usage=usage,
            in_flight=in_flight,
            policy=policy,
            unsupported=unsupported,
            subscription_models=frozenset(),
            local_in_flight=started_here,
        )
        snapshot = replace(
            draft,
            subscription_models=subscription_models(sources.model_list(), draft),
        )
        self._snapshot = snapshot
        self._loaded_at = self._clock.now()
        return snapshot

    def _kept_on_failure(self, what: str, value: T | BaseException, previous: T) -> T:
        """One slow read must not age the whole snapshot; the previous value stands in until it is older than max_stale_s."""
        now = self._clock.now()
        if not isinstance(value, BaseException):
            self._part_read_at[what] = now
            return value
        if isinstance(value, asyncio.CancelledError):
            raise value
        if now - self._part_read_at.setdefault(what, now) > self._timing.max_stale_s:
            raise value
        verbose_proxy_logger.warning(
            "agentek_gateway snapshot keeps the previous %s (%s)",
            what,
            type(value).__name__,
        )
        return previous

    def _flags_or_database(
        self, flags: Mapping[SubscriptionId, bool] | BaseException
    ) -> Mapping[SubscriptionId, bool]:
        """The database row is the source of truth for enabled; the Redis flags only speed a change up."""
        if not isinstance(flags, BaseException):
            return flags
        if isinstance(flags, asyncio.CancelledError):
            raise flags
        verbose_proxy_logger.warning(
            "agentek_gateway enabled flags unreadable (%s), using the database",
            type(flags).__name__,
        )
        return {}

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001
                verbose_proxy_logger.exception(
                    "agentek_gateway snapshot refresh failed"
                )
            try:
                await asyncio.wait_for(self._wake.wait(), self._timing.interval_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _provider_limits(self) -> Mapping[str, int | None]:
        settings = self._sources.provider_settings
        if settings is None:
            return {}
        return {
            provider: item.concurrency_limit
            for provider, item in (await settings.load()).items()
        }

    async def _subscriptions(self) -> Sequence[Subscription]:
        now = self._clock.now()
        cached = self._directory
        if cached is not None and now - cached[0] < self._timing.directory_ttl_s:
            return cached[1]
        listed = await self._sources.repo.list_subscriptions()
        self._directory = (now, listed)
        return listed
