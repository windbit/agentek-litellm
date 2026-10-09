"""Leader-side pieces (refresher, probes, lease) on shared fake Redis, with a scripted provider."""

import asyncio
from dataclasses import dataclass, field

import fakeredis

from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.credentials import InMemoryCredentialStore
from agentek_gateway.subscriptions.leader import StillLeader, always_leader
from agentek_gateway.subscriptions.memory import InMemorySubscriptionRepo
from agentek_gateway.subscriptions.model import Limits, Subscription
from agentek_gateway.subscriptions.probes import Jitter, ProbeDeps, ProbeLoop
from agentek_gateway.subscriptions.providers.base import (
    ProbeResult,
    RefreshedTokens,
    RefreshOutcome,
)
from agentek_gateway.subscriptions.providers.chatgpt import ChatgptAuth
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.refresher import RefreshDeps, TokenRefresher
from agentek_gateway.subscriptions.service import StateService
from agentek_gateway.subscriptions.state_db import InMemoryStateDb
from agentek_gateway.subscriptions.token_coordination import TokenCoordinator

from .conftest import FakeClock, make_subscription

HOUR_S = 3600.0
FRESH_PAIR = "fresh"


def half_of_limit(limit_s: float) -> float:
    return limit_s / 2


def tokens(expires_in_s: float, now: float, suffix: str = "0") -> ChatgptAuth:
    return ChatgptAuth(
        access_token=f"at-{suffix}",
        refresh_token=f"rt-{suffix}",
        account_id="acct",
        id_token=f"id-{suffix}",
        expires_at=now + expires_in_s,
    )


@dataclass
class ScriptedProvider:
    outcomes: list[RefreshOutcome] = field(default_factory=list)
    health: list[ProbeResult] = field(default_factory=list)
    usage: list[Limits | None] = field(default_factory=list)
    refresh_tokens: list[str] = field(default_factory=list)
    probes: list[str] = field(default_factory=list)
    delay_s: float = 0.01

    async def refresh(self, refresh_token: str, *, now: float) -> RefreshOutcome:
        self.refresh_tokens.append(refresh_token)
        await asyncio.sleep(self.delay_s)
        if self.outcomes:
            return self.outcomes.pop(0)
        count = len(self.refresh_tokens)
        return RefreshedTokens(f"at-new{count}", f"rt-new{count}", None, now + HOUR_S)

    async def probe_health(self, auth: ChatgptAuth, *, now: float) -> ProbeResult:
        self.probes.append(auth.access_token)
        return self.health.pop(0) if self.health else ProbeResult(True, None, None)

    async def probe_usage(self, auth: ChatgptAuth, *, now: float) -> Limits | None:
        return self.usage.pop(0) if self.usage else None


@dataclass
class Upkeep:
    clock: FakeClock
    server: fakeredis.FakeServer
    db: InMemoryStateDb
    credentials: InMemoryCredentialStore
    repo: InMemorySubscriptionRepo
    provider: ScriptedProvider
    keys: Keys
    config: GatewayConfig

    def replica(
        self,
        jitter: Jitter = half_of_limit,
        still_leader: StillLeader = always_leader,
        deadline_s: float = 40.0,
    ) -> "Replica":
        redis = fakeredis.FakeAsyncRedis(server=self.server, decode_responses=True)
        store = RedisStateStore(redis, self.db, self.clock, self.keys)
        states = StateService(store, self.clock, self.config)
        coordinator = TokenCoordinator(redis, self.keys)
        providers = {"chatgpt": self.provider}
        return Replica(
            redis=redis,
            store=store,
            states=states,
            coordinator=coordinator,
            refresher=TokenRefresher(
                RefreshDeps(
                    self.clock,
                    self.repo,
                    self.credentials,
                    coordinator,
                    store,
                    states,
                    providers,
                    still_leader,
                    deadline_s,
                )
            ),
            probes=ProbeLoop(
                ProbeDeps(
                    self.clock,
                    self.config,
                    self.repo,
                    self.credentials,
                    store,
                    states,
                    providers,
                    still_leader,
                ),
                jitter=jitter,
            ),
        )


@dataclass
class Replica:
    redis: fakeredis.FakeAsyncRedis
    store: RedisStateStore
    states: StateService
    coordinator: TokenCoordinator
    refresher: TokenRefresher
    probes: ProbeLoop


def build_upkeep(
    sub_ids: list[str], expires_in_s: float = 60.0
) -> tuple[Upkeep, list[Subscription]]:
    clock = FakeClock()
    subscriptions = [make_subscription(sub_id) for sub_id in sub_ids]
    credentials = InMemoryCredentialStore()
    for subscription in subscriptions:
        credentials.put(subscription.credential_name, tokens(expires_in_s, clock.now()))
    upkeep = Upkeep(
        clock=clock,
        server=fakeredis.FakeServer(),
        db=InMemoryStateDb(),
        credentials=credentials,
        repo=InMemorySubscriptionRepo(subscriptions),
        provider=ScriptedProvider(),
        keys=Keys("t:"),
        config=GatewayConfig(),
    )
    return upkeep, subscriptions
