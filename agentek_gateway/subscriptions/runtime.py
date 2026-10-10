from collections.abc import Mapping
from dataclasses import dataclass, field

from .attempts import AttemptTracker
from .clock import Clock
from .config import GatewayConfig
from .expiring import ExpiringMap
from .failures import FailureRouter
from .gateway import GatewayParts, SubscriptionGateway, request_key_of
from .outcomes import OutcomeTracker
from .ports import PolicyRepo, SlotStore, StateStore, SubscriptionRepo
from .providers.base import SubscriptionProvider
from .registry import AttemptRegistry
from .service import StateService
from .signals import SignalProcessor
from .slots import SlotLedger
from .snapshot import ModelList, SnapshotCache, SnapshotSources, SnapshotTiming
from .stickiness import StickyBook
from .tasks import BackgroundTasks
from .telemetry import NullTelemetry, Telemetry
from .toggle import SubscriptionToggle

LOCAL_STICKY_TTL_S = 60.0


@dataclass(frozen=True, slots=True)
class RuntimeDeps:
    clock: Clock
    config: GatewayConfig
    state_store: StateStore
    slot_store: SlotStore
    repo: SubscriptionRepo
    policy: PolicyRepo
    providers: Mapping[str, SubscriptionProvider]
    model_list: ModelList
    telemetry: Telemetry = field(default_factory=NullTelemetry)
    timing: SnapshotTiming = field(default_factory=SnapshotTiming)


@dataclass(frozen=True, slots=True)
class SubscriptionRuntime:
    parts: GatewayParts
    gateway: SubscriptionGateway
    outcomes: OutcomeTracker
    toggle: SubscriptionToggle
    states: StateService
    writes: BackgroundTasks = field(default_factory=BackgroundTasks)

    def request_key(self, request: dict[str, object]) -> str | None:
        return request_key_of(request)


def build_runtime(deps: RuntimeDeps) -> SubscriptionRuntime:
    config, clock, store = deps.config, deps.clock, deps.state_store
    ttl_s = config.defaults.slot_ttl_s
    snapshot = SnapshotCache(
        SnapshotSources(
            repo=deps.repo,
            policy=deps.policy,
            store=store,
            slots=deps.slot_store,
            model_list=deps.model_list,
        ),
        clock,
        deps.timing,
    )
    tasks, writes = BackgroundTasks(), BackgroundTasks()
    states = StateService(
        store,
        clock,
        config,
        snapshot.request_refresh,
        view=snapshot,
        spawn=writes.spawn,
    )
    signals = SignalProcessor(store, deps.repo, states, clock, config)
    parts = GatewayParts(
        clock=clock,
        config=config,
        snapshot=snapshot,
        attempts=AttemptTracker(clock, ttl_s),
        ledger=SlotLedger(deps.slot_store, clock, ttl_s),
        sticky=StickyBook(
            store, clock, config.defaults.sticky_ttl_s, LOCAL_STICKY_TTL_S
        ),
        providers=deps.providers,
        failures=FailureRouter(states, signals, store, clock, config),
        signals=signals,
        registry=AttemptRegistry(clock, ttl_s),
        telemetry=deps.telemetry,
        tasks=tasks,
        offers=ExpiringMap(clock, ttl_s),
    )
    return SubscriptionRuntime(
        parts=parts,
        gateway=SubscriptionGateway(parts),
        outcomes=OutcomeTracker(parts),
        toggle=SubscriptionToggle(store, deps.repo, states),
        states=states,
        writes=writes,
    )


@dataclass(slots=True)
class RuntimeSlot:
    runtime: SubscriptionRuntime | None = None


GLOBAL_SLOT = RuntimeSlot()
