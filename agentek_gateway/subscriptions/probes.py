import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .config import GatewayConfig
from .credentials import CredentialStore
from .events import (
    AccountDeactivated,
    Event,
    LimitExhausted,
    LimitWindow,
    ProbeFailed,
    ProbeSucceeded,
    Unauthorized,
)
from .leader import StillLeader, always_leader
from .model import (
    Limits,
    StateRecord,
    Subscription,
    SubscriptionId,
    SubscriptionState,
    effective_state,
)
from .ports import StateStore, SubscriptionRepo
from .providers.base import (
    AccountBanned,
    AuthRejected,
    LimitReached,
    ProbeResult,
)
from .providers.chatgpt import ChatgptAuth
from .service import StateService

FULL_USAGE_PERCENT = 100.0
PROBED_STATES = frozenset({SubscriptionState.HALF_OPEN, SubscriptionState.BROKEN})

Jitter = Callable[[float], float]


class ProbingProvider(Protocol):
    async def probe_health(self, auth: ChatgptAuth, *, now: float) -> ProbeResult: ...

    async def probe_usage(self, auth: ChatgptAuth, *, now: float) -> Limits | None: ...


@dataclass(frozen=True, slots=True)
class ProbeDeps:
    clock: Clock
    config: GatewayConfig
    repo: SubscriptionRepo
    credentials: CredentialStore
    store: StateStore
    states: StateService
    providers: Mapping[str, ProbingProvider]
    still_leader: StillLeader = always_leader


def random_jitter(limit_s: float) -> float:
    return random.uniform(0.0, limit_s)


class ProbeLoop:
    """Leader-side liveness probes: HALF_OPEN soon after its block ends, BROKEN rarely, each spread by jitter."""

    def __init__(self, deps: ProbeDeps, jitter: Jitter = random_jitter) -> None:
        self._deps = deps
        self._jitter = jitter
        self._due: dict[SubscriptionId, float] = {}
        self._tracked_since: dict[SubscriptionId, float] = {}

    async def tick(self) -> None:
        deps = self._deps
        now = deps.clock.now()
        states = await deps.store.read_all_states()
        subscriptions = [
            subscription
            for subscription in await deps.repo.list_subscriptions()
            if subscription.provider in deps.providers
        ]
        self._forget_recovered(subscriptions, states, now)
        for subscription in subscriptions:
            record = states.get(subscription.id)
            if record is None or not subscription.enabled:
                continue
            if self._is_due(subscription, record, now):
                if not await deps.still_leader():
                    return
                await self._probe_safely(subscription)
        for provider in {subscription.provider for subscription in subscriptions}:
            await deps.store.mark_probed(provider, deps.clock.now())

    def _forget_recovered(
        self,
        subscriptions: list[Subscription],
        states: Mapping[SubscriptionId, StateRecord],
        now: float,
    ) -> None:
        for subscription in subscriptions:
            record = states.get(subscription.id)
            if record is None or effective_state(record, now) not in PROBED_STATES:
                self._due.pop(subscription.id, None)

    def _is_due(
        self, subscription: Subscription, record: StateRecord, now: float
    ) -> bool:
        state = effective_state(record, now)
        if state not in PROBED_STATES:
            return False
        if subscription.id not in self._due:
            tuning = self._deps.config.tuning_for(subscription.provider)
            spread = self._jitter(tuning.half_open_probe_interval_s)
            waited = 0.0
            if state is SubscriptionState.BROKEN:
                waited = max(
                    0.0, record.entered_at + tuning.broken_probe_interval_s - now
                )
            self._due[subscription.id] = now + waited + spread
        return now >= self._due[subscription.id]

    async def _probe_safely(self, subscription: Subscription) -> None:
        deps = self._deps
        tuning = deps.config.tuning_for(subscription.provider)
        try:
            await self._probe(subscription)
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception(
                "agentek_gateway probe of %s failed", subscription.name
            )
        now = deps.clock.now()
        record = await deps.store.read_state(subscription.id)
        state = effective_state(record, now) if record else SubscriptionState.ACTIVE
        interval = (
            tuning.broken_probe_interval_s
            if state is SubscriptionState.BROKEN
            else tuning.half_open_probe_interval_s
        )
        self._due[subscription.id] = now + interval

    async def _probe(self, subscription: Subscription) -> None:
        deps = self._deps
        stored = await deps.credentials.read_auth(subscription.credential_name)
        provider = deps.providers[subscription.provider]
        if stored is None:
            verbose_proxy_logger.warning(
                "agentek_gateway probe skipped, %s has no tokens", subscription.name
            )
            return
        now = deps.clock.now()
        usage = await provider.probe_usage(stored.auth, now=now)
        exhausted = exhausted_event(usage)
        if exhausted is not None:
            await deps.states.apply(subscription, exhausted)
            return
        result = await provider.probe_health(stored.auth, now=now)
        await deps.states.apply(subscription, probe_event(result, usage))


def exhausted_event(limits: Limits | None) -> LimitExhausted | None:
    if limits is None:
        return None
    for window, kind in (
        (limits.weekly, LimitWindow.WEEKLY),
        (limits.five_hour, LimitWindow.FIVE_HOUR),
    ):
        if window and window.used_percent >= FULL_USAGE_PERCENT:
            return LimitExhausted(kind, window.reset_at)
    return None


def probe_event(result: ProbeResult, usage: Limits | None) -> Event:
    if result.ok:
        return ProbeSucceeded(result.limits or usage)
    match result.error:
        case LimitReached(window=window, reset_at=reset_at):
            return LimitExhausted(window, reset_at)
        case AuthRejected():
            return Unauthorized()
        case AccountBanned():
            return AccountDeactivated()
    return ProbeFailed()
