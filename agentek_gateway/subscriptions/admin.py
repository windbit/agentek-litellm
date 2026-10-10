import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .audit import AuditEntry, AuditLog
from .clock import Clock
from .credential_directory import CredentialDirectory
from .credentials import CredentialStore
from .events import LimitsObserved, Reauthorized
from .model import Limits, Subscription, SubscriptionId, UsageRecord, UsageSource
from .model_copies import CopySync
from .ports import StateStore, SubscriptionRepo
from .prisma_repos import NewSubscription, SubscriptionWriter
from .probes import exhausted_event
from .provider_settings import ProviderSettingsRepo
from .providers.chatgpt import ChatgptAuth
from .providers.chatgpt_login import DeviceLogin
from .providers.chatgpt_profile import Profile, profile_of
from .service import StateService
from .toggle import SubscriptionToggle
from .token_coordination import TokenCoordinator
from .views import (
    ProviderView,
    SubscriptionView,
    is_working_now,
    subscription_view,
)

LIMITS_REFRESH_WINDOW_S = 30.0
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


class LoginFlow(Protocol):
    async def start(self) -> DeviceLogin: ...

    async def poll(self, device_auth_id: str, user_code: str) -> ChatgptAuth | None: ...


@dataclass(frozen=True, slots=True)
class LoginTarget:
    """Where a finished sign-in goes: a new subscription under `name`, or the tokens of an existing one."""

    name: str | None = None
    subscription_id: SubscriptionId | None = None


@dataclass(frozen=True, slots=True)
class LoginPoll:
    done: bool
    subscription: SubscriptionView | None = None


@dataclass(frozen=True, slots=True)
class LimitsRefresh:
    refreshed: bool
    subscription: SubscriptionView


@dataclass(frozen=True, slots=True)
class AdminDeps:
    clock: Clock
    repo: SubscriptionRepo
    writer: SubscriptionWriter
    directory: CredentialDirectory
    credentials: CredentialStore
    store: StateStore
    toggle: SubscriptionToggle
    states: StateService
    coordinator: TokenCoordinator
    copies: CopySync
    settings: ProviderSettingsRepo
    audit: AuditLog
    usage_providers: Mapping[str, UsageProvider]
    logins: Mapping[str, LoginFlow]
    on_changed: Callable[[], None]


class SubscriptionAdmin:
    """Operator actions on subscriptions; each one is written to the audit log after it succeeded."""

    def __init__(self, deps: AdminDeps) -> None:
        self._deps = deps

    async def overview(self) -> tuple[list[ProviderView], list[SubscriptionView]]:
        deps = self._deps
        subscriptions = await deps.repo.list_subscriptions()
        views = [await self._view(subscription) for subscription in subscriptions]
        return await self._providers(subscriptions), views

    async def get(self, subscription_id: SubscriptionId) -> SubscriptionView:
        return await self._view(await self._find(subscription_id))

    async def set_enabled(
        self, actor: str, subscription_id: SubscriptionId, enabled: bool
    ) -> SubscriptionView:
        subscription = await self._find(subscription_id)
        if subscription.enabled != enabled:
            await self._deps.toggle.set_enabled(subscription, enabled)
            await self._record(
                actor,
                "enabled",
                subscription,
                {"enabled": subscription.enabled},
                {"enabled": enabled},
            )
        return await self.get(subscription_id)

    async def update_settings(
        self,
        actor: str,
        subscription_id: SubscriptionId,
        changes: Mapping[str, int | None],
    ) -> SubscriptionView:
        subscription = await self._find(subscription_id)
        _validate_settings(changes)
        before = {
            "priority": subscription.priority,
            "max_concurrency": subscription.concurrency_limit,
        }
        await self._deps.writer.update_subscription(subscription_id, changes)
        self._deps.on_changed()
        await self._record(
            actor, "settings", subscription, _kept(before, changes), dict(changes)
        )
        return await self.get(subscription_id)

    async def set_provider_concurrency(
        self, actor: str, provider: str, limit: int | None
    ) -> ProviderView:
        deps = self._deps
        if provider not in deps.logins:
            raise NotFoundError(f"unknown provider {provider}")
        _validate_limit(limit)
        before = (await deps.settings.load()).get(provider)
        await deps.settings.set_concurrency(provider, limit)
        deps.on_changed()
        await deps.audit.record(
            AuditEntry(
                actor=actor,
                action=f"{ACTION_PREFIX}provider_concurrency",
                subject=provider,
                before={
                    "concurrency_limit": before.concurrency_limit if before else None
                },
                after={"concurrency_limit": limit},
            )
        )
        subscriptions = await deps.repo.list_subscriptions()
        return next(
            view
            for view in await self._providers(subscriptions)
            if view.provider == provider
        )

    async def remove(self, actor: str, subscription_id: SubscriptionId) -> None:
        """Deletes the credential first: the importer would otherwise bring the subscription back from it."""
        deps = self._deps
        subscription = await self._find(subscription_id)
        await deps.coordinator.clear_latest(subscription.credential_name)
        await deps.directory.delete_credential(subscription.credential_name)
        await deps.writer.delete_subscription(subscription.id)
        await deps.copies.remove_subscription(subscription)
        await deps.store.forget_subscription(subscription.id)
        deps.on_changed()
        await self._record(
            actor,
            "removed",
            subscription,
            {"enabled": subscription.enabled, "priority": subscription.priority},
            None,
        )

    async def refresh_limits(
        self, actor: str, subscription_id: SubscriptionId
    ) -> LimitsRefresh:
        deps = self._deps
        subscription = await self._find(subscription_id)
        claimed = await deps.store.claim_limits_refresh(
            subscription.id, LIMITS_REFRESH_WINDOW_S
        )
        refreshed = claimed and await self._check_limits(subscription)
        if refreshed:
            await self._record(actor, "limits_refreshed", subscription, None, None)
        return LimitsRefresh(refreshed, await self._view(subscription))

    async def login_start(self, provider: str) -> DeviceLogin:
        return await self._flow(provider).start()

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
        if target.subscription_id:
            subscription = await self._reauthorize(actor, target.subscription_id, auth)
        else:
            subscription = await self._create(actor, provider, target, auth)
        return LoginPoll(done=True, subscription=await self._view(subscription))

    async def _create(
        self, actor: str, provider: str, target: LoginTarget, auth: ChatgptAuth
    ) -> Subscription:
        deps = self._deps
        name = target.name or ""
        if not await deps.directory.create_credential(name, provider, auth):
            raise ConflictError(f"name {name} is taken")
        subscription = await deps.writer.create_subscription(
            NewSubscription(provider=provider, name=name, credential_name=name)
        )
        if subscription is None:
            raise ConflictError(f"name {name} is taken")
        deps.on_changed()
        await deps.copies.run_once()
        await self._record(
            actor,
            "created",
            subscription,
            None,
            {"provider": provider, "priority": subscription.priority},
        )
        return subscription

    async def _reauthorize(
        self, actor: str, subscription_id: SubscriptionId, auth: ChatgptAuth
    ) -> Subscription:
        """New tokens win over any pair a refresh left in Redis; the old pair must not be written back over them."""
        deps = self._deps
        subscription = await self._find(subscription_id)
        name = subscription.credential_name
        await deps.directory.replace_auth(name, auth)
        await deps.coordinator.clear_latest(name)
        await deps.store.clear_unsupported(subscription.id)
        await deps.states.apply(subscription, Reauthorized())
        await self._record(actor, "reauthorized", subscription, None, None)
        return subscription

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
        if target.subscription_id:
            subscription = await self._find(target.subscription_id)
            if subscription.provider != provider:
                raise InvalidRequestError("subscription belongs to another provider")
            return
        if not target.name or not NAME_PATTERN.match(target.name):
            raise InvalidRequestError("invalid subscription name")
        known = await deps.repo.list_subscriptions()
        if any(target.name in (sub.name, sub.credential_name) for sub in known):
            raise ConflictError(f"name {target.name} is taken")

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

    async def _view(self, subscription: Subscription) -> SubscriptionView:
        deps = self._deps
        record = await deps.store.read_state(subscription.id)
        usage = (await deps.store.read_all_usage()).get(subscription.id)
        stored = await deps.credentials.read_auth(subscription.credential_name)
        profile = profile_of(stored.auth.id_token) if stored else Profile(None, None)
        return subscription_view(subscription, record, usage, profile, deps.clock.now())

    async def _providers(
        self, subscriptions: Sequence[Subscription]
    ) -> list[ProviderView]:
        deps = self._deps
        settings = await deps.settings.load()
        states = await deps.store.read_states([sub.id for sub in subscriptions])
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

    async def _record(
        self,
        actor: str,
        action: str,
        subscription: Subscription,
        before: Mapping[str, object] | None,
        after: Mapping[str, object] | None,
    ) -> None:
        try:
            await self._deps.audit.record(
                AuditEntry(
                    actor=actor,
                    action=f"{ACTION_PREFIX}{action}",
                    subscription_id=subscription.id,
                    subscription_name=subscription.name,
                    before=before,
                    after=after,
                )
            )
        except Exception:
            verbose_proxy_logger.exception(
                "agentek_gateway audit write failed after %s of %s",
                action,
                subscription.name,
            )
            raise


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
