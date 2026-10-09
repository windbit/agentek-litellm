import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import quote

from redis import Redis as SyncRedis
from redis.asyncio import Redis

from litellm._logging import verbose_proxy_logger
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from ..proxy_host import ProxyHost
from ..startup import Plugin
from .adapter import SubscriptionCallback
from .clock import Clock, SystemClock
from .config import config_from_env
from .credentials import CredentialStore, PrismaCredentialStore
from .duties import LeaderDuties
from .leader import LeaderLease
from .memory import InMemoryPolicyRepo, InMemorySubscriptionRepo
from .notify import RedisListener, RedisNotifier
from .ports import PolicyRepo, SubscriptionRepo
from .probes import ProbeDeps, ProbeLoop
from .providers.chatgpt import PROVIDER_ID, ChatGPTProvider
from .providers.observer import install_error_observer
from .providers.transport import HttpxProbeTransport
from .redis_keys import Keys
from .redis_slots import RedisSlotStore
from .redis_state import RedisStateStore
from .refresh_guard import RefreshGuard, install_refresh_guard
from .refresher import RefreshDeps, TokenRefresher
from .runtime import (
    GLOBAL_SLOT,
    RuntimeDeps,
    SubscriptionRuntime,
    build_runtime,
)
from .state_db import PrismaStateDb
from .token_coordination import SyncTokenCoordinator, TokenCoordinator

ENV_REDIS_URL = "AGENTEK_GATEWAY_REDIS_URL"
ENV_REDIS_PREFIX = "AGENTEK_GATEWAY_REDIS_PREFIX"
DEFAULT_REDIS_PREFIX = "agentek:"
RECONCILE_INTERVAL_S = 30.0


@dataclass(frozen=True, slots=True)
class Connections:
    """Outside services and records the runtime works with; supplied by the caller so each can be replaced."""

    redis: Redis
    sync_redis: SyncRedis
    credentials: CredentialStore
    repo: SubscriptionRepo
    policy: PolicyRepo


def redis_url_from_env(environ: Mapping[str, str]) -> str:
    url = environ.get(ENV_REDIS_URL) or environ.get("REDIS_URL")
    if url:
        return url
    host = environ.get("REDIS_HOST", "localhost")
    port = environ.get("REDIS_PORT", "6379")
    password = environ.get("REDIS_PASSWORD")
    credentials = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{credentials}{host}:{port}"


def redis_from_env(environ: Mapping[str, str]) -> Redis:
    return Redis.from_url(redis_url_from_env(environ), decode_responses=True)


def default_plugins() -> Sequence[Plugin]:
    return (
        Plugin(
            callback_factories=(SubscriptionCallback,),
            on_ready=(start_subscription_runtime,),
        ),
    )


async def start_subscription_runtime() -> None:
    host, environ = ProxyHost(), os.environ
    connections = Connections(
        redis=redis_from_env(environ),
        sync_redis=SyncRedis.from_url(
            redis_url_from_env(environ), decode_responses=True
        ),
        credentials=PrismaCredentialStore(host.credentials_table),
        repo=InMemorySubscriptionRepo(),
        policy=InMemoryPolicyRepo(),
    )
    GLOBAL_SLOT.runtime = await build_proxy_runtime(
        host, environ, SystemClock(), connections
    )


async def build_proxy_runtime(
    host: ProxyHost,
    environ: Mapping[str, str],
    clock: Clock,
    connections: Connections,
) -> SubscriptionRuntime:
    config = config_from_env(environ)
    redis = connections.redis
    keys = Keys(environ.get(ENV_REDIS_PREFIX, DEFAULT_REDIS_PREFIX))
    store = RedisStateStore(
        redis,
        PrismaStateDb(host.state_table),
        clock,
        keys,
        RedisNotifier(redis, keys.changes),
    )
    await store.load_durable()
    provider = ChatGPTProvider(
        HttpxProbeTransport(), config.tuning_for(PROVIDER_ID).probe_model
    )
    repo = connections.repo
    runtime = build_runtime(
        RuntimeDeps(
            clock=clock,
            config=config,
            state_store=store,
            slot_store=RedisSlotStore(redis, clock, keys.prefix),
            repo=repo,
            policy=connections.policy,
            providers={PROVIDER_ID: provider},
            model_list=host.model_list,
        )
    )
    await runtime.parts.snapshot.refresh()
    duties = LeaderDuties(
        LeaderLease(redis, keys.leader),
        ProbeLoop(
            ProbeDeps(
                clock,
                config,
                repo,
                connections.credentials,
                store,
                runtime.states,
                {PROVIDER_ID: provider},
            )
        ),
        TokenRefresher(
            RefreshDeps(
                clock,
                repo,
                connections.credentials,
                TokenCoordinator(redis, keys),
                store,
                runtime.states,
                {PROVIDER_ID: provider},
            )
        ),
        clock,
    )
    _start_loops(
        runtime,
        store,
        RedisListener(redis, keys.changes, runtime.parts.snapshot.request_refresh),
        duties,
    )
    install_refresh_guard(
        RefreshGuard(SyncTokenCoordinator(connections.sync_redis, keys))
    )
    install_error_observer(
        ChatGPTResponsesAPIConfig,
        lambda status, headers, body: provider.classify_error(
            status, headers, body, now=clock.now()
        ),
        runtime.outcomes.on_observed,
    )
    return runtime


def _start_loops(
    runtime: SubscriptionRuntime,
    store: RedisStateStore,
    listener: RedisListener,
    duties: LeaderDuties,
) -> None:
    for name, loop in (
        ("snapshot", runtime.parts.snapshot.run()),
        ("change listener", listener.run()),
        ("state reconcile", _reconcile_forever(store)),
        ("leader duties", duties.run()),
    ):
        verbose_proxy_logger.info("agentek_gateway starting %s loop", name)
        runtime.parts.tasks.spawn(loop)


async def _reconcile_forever(store: RedisStateStore) -> None:
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_S)
        try:
            await store.reconcile()
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway state reconcile failed")
