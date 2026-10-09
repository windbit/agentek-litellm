import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .model import Subscription
from .ports import PolicyRepo, SlotStore, StateStore, SubscriptionRepo
from .selection import Snapshot, subscription_of

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


@dataclass(frozen=True, slots=True)
class SnapshotTiming:
    interval_s: float = 1.0
    max_stale_s: float = 60.0
    directory_ttl_s: float = 30.0


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
        self._wake.set()

    async def refresh(self) -> Snapshot:
        sources = self._sources
        directory = await self._subscriptions()
        ids = [subscription.id for subscription in directory]
        states, usage, unsupported, flags, in_flight = await asyncio.gather(
            sources.store.read_all_states(),
            sources.store.read_all_usage(),
            sources.store.unsupported_pairs(),
            sources.store.read_enabled_flags(),
            sources.slots.in_flight(ids),
        )
        subscriptions = [
            replace(
                subscription, enabled=flags.get(subscription.id, subscription.enabled)
            )
            for subscription in directory
        ]
        policy = await sources.policy.load_policy()
        draft = Snapshot(
            subscriptions={sub.id: sub for sub in subscriptions},
            states=states,
            usage=usage,
            in_flight=in_flight,
            policy=policy,
            unsupported=unsupported,
            subscription_models=frozenset(),
        )
        snapshot = replace(
            draft,
            subscription_models=subscription_models(sources.model_list(), draft),
        )
        self._snapshot = snapshot
        self._loaded_at = self._clock.now()
        return snapshot

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

    async def _subscriptions(self) -> Sequence[Subscription]:
        now = self._clock.now()
        cached = self._directory
        if cached is not None and now - cached[0] < self._timing.directory_ttl_s:
            return cached[1]
        listed = await self._sources.repo.list_subscriptions()
        self._directory = (now, listed)
        return listed
