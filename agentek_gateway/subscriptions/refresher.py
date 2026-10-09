import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .credentials import CredentialStore, StoredAuth
from .events import RefreshSucceeded, TokenRevoked
from .leader import StillLeader, always_leader
from .model import StateRecord, Subscription, SubscriptionState, effective_state
from .ports import StateStore, SubscriptionRepo
from .providers.base import RefreshedTokens, RefreshOutcome, RefreshRejected
from .providers.chatgpt import ChatgptAuth
from .service import StateService
from .token_coordination import LatestAuth, TokenCoordinator

REFRESH_LEAD_S = 10 * 60
RECENT_REFRESH_WINDOW_S = 60.0
REFRESH_DEADLINE_S = 40.0


class RefreshingProvider(Protocol):
    async def refresh(self, refresh_token: str, *, now: float) -> RefreshOutcome: ...


@dataclass(frozen=True, slots=True)
class RefreshDeps:
    clock: Clock
    repo: SubscriptionRepo
    credentials: CredentialStore
    coordinator: TokenCoordinator
    store: StateStore
    states: StateService
    providers: Mapping[str, RefreshingProvider]
    still_leader: StillLeader = always_leader
    deadline_s: float = REFRESH_DEADLINE_S


class TokenRefresher:
    """Leader-side token upkeep: refresh ahead of expiry under a lock, then compare-and-set into the database."""

    def __init__(self, deps: RefreshDeps) -> None:
        self._deps = deps

    async def tick(self) -> None:
        deps = self._deps
        states = await deps.store.read_all_states()
        for subscription in await deps.repo.list_subscriptions():
            if not subscription.enabled or subscription.provider not in deps.providers:
                continue
            if not await deps.still_leader():
                return
            try:
                await self._upkeep(subscription, states.get(subscription.id))
            except Exception:  # noqa: BLE001
                verbose_proxy_logger.exception(
                    "agentek_gateway token upkeep failed for %s", subscription.name
                )

    async def _upkeep(
        self, subscription: Subscription, record: StateRecord | None
    ) -> None:
        deps = self._deps
        stored = await deps.credentials.read_auth(subscription.credential_name)
        if stored is None:
            return
        stored = await self._persist_unsaved(subscription, stored)
        if self._is_due(stored, record):
            await self.refresh(subscription)

    def _is_due(self, stored: StoredAuth, record: StateRecord | None) -> bool:
        now = self._deps.clock.now()
        forced = (
            record is not None
            and effective_state(record, now) is SubscriptionState.AUTH_REFRESHING
        )
        expires_at = stored.auth.expires_at
        return forced or expires_at is None or expires_at - now <= REFRESH_LEAD_S

    async def _persist_unsaved(
        self, subscription: Subscription, stored: StoredAuth
    ) -> StoredAuth:
        """A pair a refresh produced but the database never received (crash, outage) is written first."""
        deps, name = self._deps, subscription.credential_name
        latest = await deps.coordinator.read_latest(name)
        if latest is None or latest.persisted:
            return stored
        if same_tokens(latest.auth, stored.auth):
            await deps.coordinator.save_latest(name, LatestAuth(latest.auth, True))
            return stored
        if await deps.credentials.write_auth_if_unchanged(name, stored, latest.auth):
            await deps.coordinator.save_latest(name, LatestAuth(latest.auth, True))
        else:
            await deps.coordinator.clear_latest(name)
        return await deps.credentials.read_auth(name) or stored

    async def refresh(self, subscription: Subscription) -> None:
        deps, name = self._deps, subscription.credential_name
        lock = await deps.coordinator.acquire(name)
        if lock is None:
            return
        try:
            stored = await deps.credentials.read_auth(name)
            latest = await deps.coordinator.read_latest(name)
            if stored is None or (latest and not latest.persisted):
                return
            outcome = await asyncio.wait_for(
                deps.providers[subscription.provider].refresh(
                    stored.auth.refresh_token, now=deps.clock.now()
                ),
                deps.deadline_s,
            )
            await self._apply_outcome(subscription, stored, outcome)
        finally:
            await deps.coordinator.release(name, lock)

    async def _apply_outcome(
        self, subscription: Subscription, stored: StoredAuth, outcome: RefreshOutcome
    ) -> None:
        deps, name = self._deps, subscription.credential_name
        match outcome:
            case RefreshedTokens() as tokens:
                renewed = renewed_auth(stored.auth, tokens)
                await deps.coordinator.save_latest(name, LatestAuth(renewed, False))
                await deps.store.mark_refreshed(name, RECENT_REFRESH_WINDOW_S)
                if await deps.credentials.write_auth_if_unchanged(
                    name, stored, renewed
                ):
                    await deps.coordinator.save_latest(name, LatestAuth(renewed, True))
                else:
                    await deps.coordinator.clear_latest(name)
                await deps.states.apply(subscription, RefreshSucceeded())
            case RefreshRejected(permanent=True):
                await deps.states.apply(subscription, TokenRevoked())
            case RefreshRejected():
                verbose_proxy_logger.warning(
                    "agentek_gateway token refresh of %s failed, will retry",
                    subscription.name,
                )


def renewed_auth(previous: ChatgptAuth, tokens: RefreshedTokens) -> ChatgptAuth:
    return ChatgptAuth(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        account_id=previous.account_id,
        id_token=tokens.id_token or previous.id_token,
        expires_at=tokens.expires_at,
    )


def same_tokens(first: ChatgptAuth, second: ChatgptAuth) -> bool:
    return (
        first.access_token == second.access_token
        and first.refresh_token == second.refresh_token
    )
