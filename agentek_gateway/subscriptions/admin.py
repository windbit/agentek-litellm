import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .audit import AuditEntry
from .clock import Clock
from .credential_directory import CredentialDirectory
from .credential_runtime import CredentialRuntime
from .credentials import CredentialStore
from .events import LimitsObserved, Reauthorized
from .model import (
    Limits,
    StateRecord,
    Subscription,
    SubscriptionId,
    UsageRecord,
    UsageSource,
)
from .model_copies import CopySync
from .ports import SlotStore, StateStore, SubscriptionRepo
from .prisma_repos import NewSubscription
from .probes import exhausted_event
from .provider_settings import ProviderSettingsRepo
from .providers.chatgpt import ChatgptAuth
from .providers.chatgpt_login import DeviceLogin
from .providers.chatgpt_profile import Profile, profile_of
from .service import StateService
from .stats import utc_day
from .stats_report import MAX_DAYS, StatsReport
from .toggle import SubscriptionToggle
from .unit import Unit
from .token_coordination import RECENT_REFRESH_WINDOW_S, TokenCoordinator
from .views import (
    ProviderView,
    SubscriptionView,
    is_working_now,
    subscription_view,
)

LIMITS_REFRESH_WINDOW_S = 30.0
DEFAULT_LOCK_WAIT_S = 10.0
REAUTH_LOCK_POLL_S = 0.2
REFRESHED = "refreshed"
CACHED = "cached"
UNAVAILABLE = "unavailable"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ACTION_PREFIX = "subscription."
EDITABLE_SETTINGS = ("priority", "max_concurrency")


class AdminError(Exception):
    """Base of the errors an operator action can end with."""


class NotFoundError(AdminError):
    pass


class ConflictError(AdminError):
    pass


class InvalidRequestError(AdminError):
    pass


class UsageProvider(Protocol):
    async def probe_usage(self, auth: ChatgptAuth, *, now: float) -> Limits | None: ...


class StatsSource(Protocol):
    async def report(
        self, subscriptions: Sequence[Subscription], days: int, today: date
    ) -> Mapping[SubscriptionId, StatsReport]: ...


class LoginFlow(Protocol):
    async def start(self) -> DeviceLogin: ...

    async def poll(self, device_auth_id: str, user_code: str) -> ChatgptAuth | None: ...


@dataclass(frozen=True, slots=True)
class NewSubscriptionTarget:
    name: str


@dataclass(frozen=True, slots=True)
class ReauthorizeTarget:
    subscription_id: SubscriptionId


LoginTarget = NewSubscriptionTarget | ReauthorizeTarget


@dataclass(frozen=True, slots=True)
class LoginPoll:
    done: bool
    subscription: SubscriptionView | None = None


@dataclass(frozen=True, slots=True)
class LimitsRefresh:
    status: str
    subscription: SubscriptionView

    @property
    def refreshed(self) -> bool:
        return self.status == REFRESHED


@dataclass(frozen=True, slots=True)
class AdminDeps:
    clock: Clock
    repo: SubscriptionRepo
    unit: Unit
    directory: CredentialDirectory
    runtime: CredentialRuntime
    credentials: CredentialStore
    store: StateStore
    toggle: SubscriptionToggle
    states: StateService
    coordinator: TokenCoordinator
    copies: CopySync
    settings: ProviderSettingsRepo
    usage_providers: Mapping[str, UsageProvider]
    logins: Mapping[str, LoginFlow]
    on_changed: Callable[[], None]
    slots: SlotStore
    stats: StatsSource
    lock_wait_s: float = DEFAULT_LOCK_WAIT_S


class SubscriptionAdmin:
    """Operator actions on subscriptions.

    An action that writes the database does so in one transaction with its audit entry; Redis and the
    in-memory credentials follow once the transaction committed.
    """

    def __init__(self, deps: AdminDeps) -> None:
        self._deps = deps

    async def overview(self) -> tuple[list[ProviderView], list[SubscriptionView]]:
        deps = self._deps
        subscriptions = await deps.repo.list_subscriptions()
        states = await deps.store.read_states([sub.id for sub in subscriptions])
        usage = await deps.store.read_all_usage()
        in_flight = await deps.slots.in_flight([sub.id for sub in subscriptions])
        views = await asyncio.gather(
            *(
                self._view(
                    sub, states.get(sub.id), usage.get(sub.id), in_flight.get(sub.id, 0)
                )
                for sub in subscriptions
            )
        )
        return await self._providers(subscriptions, states), list(views)

    async def stats(self, days: int) -> dict[SubscriptionId, StatsReport]:
        """Statistics of every subscription for the last days, today included."""
        deps = self._deps
        if not 1 <= days <= MAX_DAYS:
            raise InvalidRequestError(f"days must be between 1 and {MAX_DAYS}")
        subscriptions = await deps.repo.list_subscriptions()
        return dict(
            await deps.stats.report(subscriptions, days, utc_day(deps.clock.now()))
        )

    async def get(self, subscription_id: SubscriptionId) -> SubscriptionView:
        return await self._view_of(await self._find(subscription_id))

    async def set_enabled(
        self, actor: str, subscription_id: SubscriptionId, enabled: bool
    ) -> SubscriptionView:
        """Switching to the current value still repairs the state and the Redis flag, but is not audited."""
        deps = self._deps
        subscription = await self._find(subscription_id)
        if subscription.enabled != enabled:
            async with deps.unit() as writes:
                await writes.writer.update_subscription(
                    subscription_id, {"enabled": enabled}
                )
                await writes.audit.record(
                    _entry(
                        actor,
                        "enabled",
                        subscription,
                        {"enabled": subscription.enabled},
                        {"enabled": enabled},
                    )
                )
        await deps.toggle.set_enabled(subscription, enabled)
        return await self.get(subscription_id)

    async def update_settings(
        self,
        actor: str,
        subscription_id: SubscriptionId,
        changes: Mapping[str, int | None],
    ) -> SubscriptionView:
        deps = self._deps
        subscription = await self._find(subscription_id)
        _validate_settings(changes)
        before = {
            "priority": subscription.priority,
            "max_concurrency": subscription.concurrency_limit,
        }
        async with deps.unit() as writes:
            await writes.writer.update_subscription(subscription_id, changes)
            await writes.audit.record(
                _entry(
                    actor,
                    "settings",
                    subscription,
                    _kept(before, changes),
                    dict(changes),
                )
            )
        deps.on_changed()
        return await self.get(subscription_id)

    async def set_provider_concurrency(
        self, actor: str, provider: str, limit: int | None
    ) -> ProviderView:
        deps = self._deps
        if provider not in deps.logins:
            raise NotFoundError(f"unknown provider {provider}")
        _validate_limit(limit)
        before = (await deps.settings.load()).get(provider)
        async with deps.unit() as writes:
            await writes.settings.set_concurrency(provider, limit)
            await writes.audit.record(
                AuditEntry(
                    actor=actor,
                    action=f"{ACTION_PREFIX}provider_concurrency",
                    subject=provider,
                    before={
                        "concurrency_limit": (
                            before.concurrency_limit if before else None
                        )
                    },
                    after={"concurrency_limit": limit},
                )
            )
        deps.on_changed()
        subscriptions = await deps.repo.list_subscriptions()
        states = await deps.store.read_states([sub.id for sub in subscriptions])
        return next(
            view
            for view in await self._providers(subscriptions, states)
            if view.provider == provider
        )

    async def remove(self, actor: str, subscription_id: SubscriptionId) -> None:
        """Traffic stops first. The credential goes before the subscription row: the importer would otherwise bring the subscription back from it."""
        deps = self._deps
        subscription = await self._find(subscription_id)
        await deps.toggle.set_enabled(subscription, False)
        async with deps.unit() as writes:
            await writes.directory.delete_credential(subscription.credential_name)
            await writes.writer.delete_subscription(subscription.id)
            await writes.audit.record(
                _entry(
                    actor,
                    "removed",
                    subscription,
                    {
                        "enabled": subscription.enabled,
                        "priority": subscription.priority,
                    },
                    None,
                )
            )
        await deps.coordinator.clear_latest(subscription.credential_name)
        await deps.copies.remove_subscription(subscription)
        await deps.store.forget_subscription(subscription.id)
        deps.on_changed()

    async def refresh_limits(
        self, actor: str, subscription_id: SubscriptionId
    ) -> LimitsRefresh:
        deps = self._deps
        subscription = await self._find(subscription_id)
        if not await deps.store.claim_limits_refresh(
            subscription.id, LIMITS_REFRESH_WINDOW_S
        ):
            return LimitsRefresh(CACHED, await self._view_of(subscription))
        if not await self._check_limits(subscription):
            return LimitsRefresh(UNAVAILABLE, await self._view_of(subscription))
        async with deps.unit() as writes:
            await writes.audit.record(
                _entry(actor, "limits_refreshed", subscription, None, None)
            )
        return LimitsRefresh(REFRESHED, await self._view_of(subscription))

    async def login_start(self, actor: str, provider: str) -> DeviceLogin:
        """The intent is recorded before the provider issues anything, so a code that gets exchanged is always on record."""
        deps = self._deps
        flow = self._flow(provider)
        async with deps.unit() as writes:
            await writes.audit.record(
                AuditEntry(
                    actor=actor,
                    action=f"{ACTION_PREFIX}login_started",
                    subject=provider,
                )
            )
        return await flow.start()

    async def login_poll(
        self,
        actor: str,
        provider: str,
        device_auth_id: str,
        user_code: str,
        target: LoginTarget,
    ) -> LoginPoll:
        flow = self._flow(provider)
        await self._check_target(provider, target)
        auth = await flow.poll(device_auth_id, user_code)
        if auth is None:
            return LoginPoll(done=False)
        match target:
            case ReauthorizeTarget(subscription_id=subscription_id):
                subscription = await self._reauthorize(actor, subscription_id, auth)
            case NewSubscriptionTarget(name=name):
                subscription = await self._create(actor, provider, name, auth)
        return LoginPoll(done=True, subscription=await self._view_of(subscription))

    async def _create(
        self, actor: str, provider: str, name: str, auth: ChatgptAuth
    ) -> Subscription:
        deps = self._deps
        async with deps.unit() as writes:
            if not await writes.directory.create_credential(name, provider, auth):
                raise ConflictError(f"name {name} is taken")
            subscription = await writes.writer.create_subscription(
                NewSubscription(provider=provider, name=name, credential_name=name)
            )
            if subscription is None:
                raise ConflictError(f"name {name} is taken")
            await writes.audit.record(
                _entry(
                    actor,
                    "created",
                    subscription,
                    None,
                    {"provider": provider, "priority": subscription.priority},
                )
            )
        deps.runtime.apply(name, provider, auth)
        deps.on_changed()
        await deps.copies.run_once()
        return subscription

    async def _reauthorize(
        self, actor: str, subscription_id: SubscriptionId, auth: ChatgptAuth
    ) -> Subscription:
        """Runs under the credential's refresh lock, so a refresh cannot write its older pair over the new tokens.

        The worker's own credentials follow before the state event: the liveness probe the event triggers must already use the new tokens.
        Replicas that still hold the old tokens for up to LiteLLM's reload interval may get a 401; the shared mark makes them ignore it like a 401 right after a refresh.
        """
        deps = self._deps
        subscription = await self._find(subscription_id)
        name = subscription.credential_name
        lock = await self._acquire_refresh_lock(name)
        try:
            async with deps.unit() as writes:
                if not await writes.directory.replace_auth(name, auth):
                    raise NotFoundError(f"credential of {subscription.name} not found")
                await writes.audit.record(
                    _entry(actor, "reauthorized", subscription, None, None)
                )
            await deps.coordinator.clear_latest(name)
            deps.runtime.apply(name, subscription.provider, auth)
            await deps.store.mark_refreshed(name, RECENT_REFRESH_WINDOW_S)
            await deps.store.clear_unsupported(subscription.id)
            await deps.states.apply(subscription, Reauthorized())
        finally:
            await deps.coordinator.release(name, lock)
        return subscription

    async def _acquire_refresh_lock(self, credential_name: str) -> str:
        waited = 0.0
        while waited <= self._deps.lock_wait_s:
            lock = await self._deps.coordinator.acquire(credential_name)
            if lock is not None:
                return lock
            await asyncio.sleep(REAUTH_LOCK_POLL_S)
            waited += REAUTH_LOCK_POLL_S
        raise ConflictError("a token refresh of this subscription is still running")

    async def _check_limits(self, subscription: Subscription) -> bool:
        deps = self._deps
        stored = await deps.credentials.read_auth(subscription.credential_name)
        provider = deps.usage_providers.get(subscription.provider)
        if stored is None or provider is None:
            return False
        now = deps.clock.now()
        limits = await provider.probe_usage(stored.auth, now=now)
        if limits is None:
            return False
        await deps.store.write_usage(
            subscription.id, UsageRecord(limits, now, UsageSource.USAGE_CHECK)
        )
        event = exhausted_event(limits) or LimitsObserved(limits)
        await deps.states.apply(subscription, event)
        return True

    async def _check_target(self, provider: str, target: LoginTarget) -> None:
        deps = self._deps
        match target:
            case ReauthorizeTarget(subscription_id=subscription_id):
                subscription = await self._find(subscription_id)
                if subscription.provider != provider:
                    raise InvalidRequestError(
                        "subscription belongs to another provider"
                    )
            case NewSubscriptionTarget(name=name):
                if not NAME_PATTERN.match(name):
                    raise InvalidRequestError("invalid subscription name")
                known = await deps.repo.list_subscriptions()
                credentials = await deps.directory.list_credentials(provider)
                taken = {sub.name for sub in known} | {
                    sub.credential_name for sub in known
                }
                taken |= {credential.name for credential in credentials}
                if name in taken:
                    raise ConflictError(f"name {name} is taken")

    def _flow(self, provider: str) -> LoginFlow:
        flow = self._deps.logins.get(provider)
        if flow is None:
            raise NotFoundError(f"unknown provider {provider}")
        return flow

    async def _find(self, subscription_id: SubscriptionId) -> Subscription:
        for subscription in await self._deps.repo.list_subscriptions():
            if subscription.id == subscription_id:
                return subscription
        raise NotFoundError(f"subscription {subscription_id} not found")

    async def _view_of(self, subscription: Subscription) -> SubscriptionView:
        deps = self._deps
        record = await deps.store.read_state(subscription.id)
        usage = (await deps.store.read_all_usage()).get(subscription.id)
        in_flight = (await deps.slots.in_flight([subscription.id])).get(
            subscription.id, 0
        )
        return await self._view(subscription, record, usage, in_flight)

    async def _view(
        self,
        subscription: Subscription,
        record: StateRecord | None,
        usage: UsageRecord | None,
        in_flight: int,
    ) -> SubscriptionView:
        deps = self._deps
        stored = await deps.credentials.read_auth(subscription.credential_name)
        profile = profile_of(stored.auth.id_token) if stored else Profile(None, None)
        return subscription_view(
            subscription, record, usage, profile, deps.clock.now(), in_flight=in_flight
        )

    async def _providers(
        self,
        subscriptions: Sequence[Subscription],
        states: Mapping[SubscriptionId, StateRecord],
    ) -> list[ProviderView]:
        deps = self._deps
        settings = await deps.settings.load()
        now = deps.clock.now()
        views = []
        for provider in sorted(deps.logins):
            own = [sub for sub in subscriptions if sub.provider == provider]
            configured = settings.get(provider)
            views.append(
                ProviderView(
                    provider=provider,
                    concurrency_limit=(
                        configured.concurrency_limit if configured else None
                    ),
                    subscriptions=len(own),
                    working=sum(
                        is_working_now(sub, states.get(sub.id), now) for sub in own
                    ),
                )
            )
        return views


def _entry(
    actor: str,
    action: str,
    subscription: Subscription,
    before: Mapping[str, object] | None,
    after: Mapping[str, object] | None,
) -> AuditEntry:
    return AuditEntry(
        actor=actor,
        action=f"{ACTION_PREFIX}{action}",
        subscription_id=subscription.id,
        subscription_name=subscription.name,
        before=before,
        after=after,
    )


def _validate_settings(changes: Mapping[str, int | None]) -> None:
    unknown = set(changes) - set(EDITABLE_SETTINGS)
    if unknown or not changes:
        raise InvalidRequestError("nothing to change")
    if changes.get("priority", 0) is None:
        raise InvalidRequestError("priority cannot be empty")
    if "max_concurrency" in changes:
        _validate_limit(changes["max_concurrency"])


def _validate_limit(limit: int | None) -> None:
    if limit is not None and limit < 1:
        raise InvalidRequestError("concurrency limit must be positive")


def _kept(
    before: Mapping[str, object], changes: Mapping[str, object]
) -> dict[str, object]:
    return {key: before[key] for key in changes}


@dataclass(slots=True)
class AdminSlot:
    admin: SubscriptionAdmin | None = None


ADMIN_SLOT = AdminSlot()
