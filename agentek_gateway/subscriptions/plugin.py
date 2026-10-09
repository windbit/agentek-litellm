import asyncio
import os
from collections.abc import Mapping, Sequence

from redis.asyncio import Redis

from litellm._logging import verbose_proxy_logger
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from ..proxy_host import ProxyHost
from ..startup import Plugin
from .adapter import SubscriptionCallback
from .clock import Clock, SystemClock
from .config import GatewayConfig, config_from_env
from .memory import InMemoryPolicyRepo, InMemorySubscriptionRepo
from .notify import RedisListener, RedisNotifier
from .providers.chatgpt import PROVIDER_ID, ChatGPTProvider
from .providers.observer import install_error_observer
from .providers.transport import HttpxProbeTransport
from .redis_keys import Keys
from .redis_slots import RedisSlotStore
from .redis_state import RedisStateStore
from .runtime import (
    GLOBAL_SLOT,
    RuntimeDeps,
    SubscriptionRuntime,
    build_runtime,
)
from .state_db import PrismaStateDb

ENV_REDIS_URL = "AGENTEK_GATEWAY_REDIS_URL"
ENV_REDIS_PREFIX = "AGENTEK_GATEWAY_REDIS_PREFIX"
DEFAULT_REDIS_PREFIX = "agentek:"
RECONCILE_INTERVAL_S = 30.0


def redis_from_env(environ: Mapping[str, str]) -> Redis:
    url = environ.get(ENV_REDIS_URL) or environ.get("REDIS_URL")
    if url:
        return Redis.from_url(url, decode_responses=True)
    return Redis(
        host=environ.get("REDIS_HOST", "localhost"),
        port=int(environ.get("REDIS_PORT", "6379")),
        password=environ.get("REDIS_PASSWORD") or None,
        decode_responses=True,
    )


def default_plugins() -> Sequence[Plugin]:
    return (
        Plugin(
            callback_factories=(SubscriptionCallback,),
            on_ready=(start_subscription_runtime,),
        ),
    )


async def start_subscription_runtime() -> None:
    runtime = await build_proxy_runtime(ProxyHost(), os.environ, SystemClock())
    GLOBAL_SLOT.runtime = runtime


async def build_proxy_runtime(
    host: ProxyHost,
    environ: Mapping[str, str],
    clock: Clock,
    redis: Redis | None = None,
) -> SubscriptionRuntime:
    config = config_from_env(environ)
    redis = redis or redis_from_env(environ)
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
    runtime = build_runtime(
        RuntimeDeps(
            clock=clock,
            config=config,
            state_store=store,
            slot_store=RedisSlotStore(redis, clock, keys.prefix),
            repo=InMemorySubscriptionRepo(),
            policy=InMemoryPolicyRepo(),
            providers={PROVIDER_ID: provider},
            model_list=host.model_list,
        )
    )
    await runtime.parts.snapshot.refresh()
    _start_loops(runtime, store, redis, keys, config)
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
    redis: Redis,
    keys: Keys,
    config: GatewayConfig,
) -> None:
    snapshot = runtime.parts.snapshot
    listener = RedisListener(redis, keys.changes, snapshot.request_refresh)
    for name, loop in (
        ("snapshot", snapshot.run()),
        ("change listener", listener.run()),
        ("state reconcile", _reconcile_forever(store)),
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
