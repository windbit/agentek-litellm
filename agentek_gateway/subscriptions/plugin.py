import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from urllib.parse import quote

from redis import Redis as SyncRedis
from redis.asyncio import Redis

from litellm._logging import verbose_proxy_logger
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from ..proxy_host import ProxyHost
from ..startup import Plugin
from .adapter import SubscriptionCallback
from .admin import ADMIN_SLOT, AdminDeps, SubscriptionAdmin
from .audit import PrismaAuditLog
from .catalog import Catalog, CatalogUpkeep
from .clock import Clock, SystemClock
from .config import config_from_env
from .credential_directory import PrismaCredentialDirectory
from .credential_runtime import LiteLLMCredentialRuntime
from .unit import PrismaUnit
from .credentials import CredentialStore, PrismaCredentialStore
from .duties import LeaderDuties
from .credential_pairs import CredentialPairs, CredentialPairsLoop
from .egress import EgressWatcher
from .importer import CredentialImporter
from .leader import LeaderLease
from .litellm_deployments import PrismaModelStore
from .model_copies import CopySync
from .notify import RedisListener, RedisNotifier
from .ports import PolicyRepo, SubscriptionRepo
from .prisma_repos import (
    PrismaPolicyRepo,
    PrismaSubscriptionRepo,
    PrismaSubscriptionWriter,
)
from .provider_settings import PrismaProviderSettingsRepo
from .probes import ProbeDeps, ProbeLoop
from .prometheus_telemetry import PrometheusTelemetry, TelemetryLoop
from .providers.chatgpt import PROVIDER_ID, ChatGPTProvider
from .providers.chatgpt_login import ChatgptLogin
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
from .subscription_cache import CachedSubscriptionRepo
from .token_coordination import SyncTokenCoordinator, TokenCoordinator

ENV_REDIS_URL = "AGENTEK_GATEWAY_REDIS_URL"
ENV_REDIS_PREFIX = "AGENTEK_GATEWAY_REDIS_PREFIX"
DEFAULT_REDIS_PREFIX = "agentek:"
RECONCILE_INTERVAL_S = 30.0
REDIS_TIMEOUT_S = 2.0
SHUTDOWN_DRAIN_S = 10.0


@dataclass(frozen=True, slots=True)
class Connections:
    """Outside services and records the runtime works with; supplied by the caller so each can be replaced."""

    redis: Redis
    sync_redis: SyncRedis
    credentials: CredentialStore
    repo: SubscriptionRepo
    policy: PolicyRepo
    catalog: Catalog | None = None


@dataclass(frozen=True, slots=True)
class Wiring:
    """Pieces the background duties and the operator API are assembled from."""

    runtime: SubscriptionRuntime
    store: RedisStateStore
    connections: Connections
    keys: Keys
    transport: HttpxProbeTransport
    provider: ChatGPTProvider
    source_repo: SubscriptionRepo


def redis_url_from_env(environ: Mapping[str, str]) -> str:
    url = environ.get(ENV_REDIS_URL) or environ.get("REDIS_URL")
    if url:
        return url
    host = environ.get("REDIS_HOST", "localhost")
    port = environ.get("REDIS_PORT", "6379")
    database = environ.get("REDIS_DB", "0")
    password = environ.get("REDIS_PASSWORD")
    credentials = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{credentials}{host}:{port}/{database}"


def redis_from_env(environ: Mapping[str, str]) -> Redis:
    return Redis.from_url(
        redis_url_from_env(environ),
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT_S,
        socket_connect_timeout=REDIS_TIMEOUT_S,
    )


def sync_redis_from_env(environ: Mapping[str, str]) -> SyncRedis:
    return SyncRedis.from_url(
        redis_url_from_env(environ),
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT_S,
        socket_connect_timeout=REDIS_TIMEOUT_S,
    )


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
        sync_redis=sync_redis_from_env(environ),
        credentials=PrismaCredentialStore(host.credentials_table),
        repo=PrismaSubscriptionRepo(host.subscription_table),
        policy=PrismaPolicyRepo(host.policy_table),
        catalog=Catalog(
            directory=PrismaCredentialDirectory(host.directory_table),
            writer=PrismaSubscriptionWriter(host.subscription_table),
            models=PrismaModelStore(host.model_table, host.proxy_db),
            audit=PrismaAuditLog(host.audit_table),
            settings=PrismaProviderSettingsRepo(host.config_table),
            unit=PrismaUnit(lambda: host.proxy_db().db),  # type: ignore[arg-type,return-value]
            runtime=LiteLLMCredentialRuntime(),
        ),
    )
    runtime = await build_proxy_runtime(host, environ, SystemClock(), connections)
    GLOBAL_SLOT.runtime = runtime
    host.on_shutdown(lambda: runtime.writes.drain_within(SHUTDOWN_DRAIN_S))


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
    transport = HttpxProbeTransport()
    provider = ChatGPTProvider(transport, config.tuning_for(PROVIDER_ID).probe_model)
    telemetry = PrometheusTelemetry()
    source_repo = connections.repo
    connections = replace(connections, repo=CachedSubscriptionRepo(source_repo, clock))
    repo = connections.repo
    stored_pairs = CredentialPairs()
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
            provider_settings=(
                connections.catalog.settings if connections.catalog else None
            ),
            telemetry=telemetry,
        )
    )
    await runtime.parts.snapshot.refresh()
    wiring = Wiring(runtime, store, connections, keys, transport, provider, source_repo)
    upkeep = await _start_catalog(wiring)
    background = Background(
        listener=RedisListener(
            redis, keys.changes, runtime.parts.snapshot.request_refresh
        ),
        duties=_leader_duties(wiring, upkeep),
        telemetry=TelemetryLoop(
            runtime.parts.snapshot, store, runtime.parts.egress, telemetry, clock
        ),
        store=store,
        repo=source_repo,
        pairs=CredentialPairsLoop(stored_pairs, source_repo, connections.credentials),
    )
    await background.pairs.refresh_once()
    _start_loops(runtime, background)
    install_refresh_guard(
        RefreshGuard(SyncTokenCoordinator(connections.sync_redis, keys), stored_pairs)
    )
    install_error_observer(
        ChatGPTResponsesAPIConfig,
        lambda status, headers, body: provider.classify_error(
            status, headers, body, now=clock.now()
        ),
        runtime.outcomes.on_observed,
    )
    return runtime


def _leader_duties(wiring: Wiring, upkeep: CatalogUpkeep | None) -> LeaderDuties:
    runtime, store, connections = wiring.runtime, wiring.store, wiring.connections
    keys, transport = wiring.keys, wiring.transport
    parts = runtime.parts
    providers = {PROVIDER_ID: parts.providers[PROVIDER_ID]}
    repo, credentials = connections.repo, connections.credentials
    lease = LeaderLease(connections.redis, keys.leader)
    return LeaderDuties(
        lease,
        ProbeLoop(
            ProbeDeps(
                parts.clock,
                parts.config,
                repo,
                credentials,
                store,
                runtime.states,
                providers,
                lease.hold,
            )
        ),
        TokenRefresher(
            RefreshDeps(
                parts.clock,
                repo,
                credentials,
                TokenCoordinator(connections.redis, keys),
                store,
                runtime.states,
                providers,
                lease.hold,
            )
        ),
        EgressWatcher(transport, store, repo, parts.clock),
        parts.clock,
        upkeep,
    )


async def _start_catalog(wiring: Wiring) -> CatalogUpkeep | None:
    """Opens the operator API and imports existing credentials; every replica does it, the unique names keep it safe."""
    runtime, connections = wiring.runtime, wiring.connections
    catalog, repo, source_repo = (
        connections.catalog,
        connections.repo,
        wiring.source_repo,
    )
    if catalog is None:
        return None
    parts = runtime.parts
    copies = CopySync(catalog.models, source_repo)
    importer = CredentialImporter(
        catalog.directory,
        source_repo,
        catalog.writer,
        runtime.toggle,
        catalog.audit,
        catalog.models,
    )
    upkeep = CatalogUpkeep(importer, copies, [PROVIDER_ID])

    def on_changed() -> None:
        if isinstance(repo, CachedSubscriptionRepo):
            repo.invalidate()
        parts.snapshot.request_refresh()

    admin = SubscriptionAdmin(
        AdminDeps(
            clock=parts.clock,
            repo=source_repo,
            unit=catalog.unit,
            directory=catalog.directory,
            runtime=catalog.runtime,
            credentials=connections.credentials,
            store=wiring.store,
            toggle=runtime.toggle,
            states=runtime.states,
            coordinator=TokenCoordinator(connections.redis, wiring.keys),
            copies=copies,
            settings=catalog.settings,
            usage_providers={PROVIDER_ID: wiring.provider},
            logins={PROVIDER_ID: ChatgptLogin(wiring.transport)},
            on_changed=on_changed,
        )
    )
    await upkeep.import_at_start()
    ADMIN_SLOT.admin = admin
    return upkeep


@dataclass(frozen=True, slots=True)
class Background:
    listener: RedisListener
    duties: LeaderDuties
    telemetry: TelemetryLoop
    store: RedisStateStore
    repo: SubscriptionRepo
    pairs: CredentialPairsLoop


def _start_loops(runtime: SubscriptionRuntime, background: Background) -> None:
    for name, loop in (
        ("snapshot", runtime.parts.snapshot.run()),
        ("change listener", background.listener.run()),
        ("state reconcile", _reconcile_forever(background.store, background.repo)),
        ("leader duties", background.duties.run()),
        ("metrics", background.telemetry.run()),
        ("credential pairs", background.pairs.run()),
    ):
        verbose_proxy_logger.info("agentek_gateway starting %s loop", name)
        runtime.parts.tasks.spawn(loop)


async def _reconcile_forever(
    store: RedisStateStore,
    repo: SubscriptionRepo,
    interval_s: float = RECONCILE_INTERVAL_S,
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            subscriptions = await repo.list_subscriptions()
            await store.reconcile([sub.id for sub in subscriptions])
            await store.reconcile_enabled_flags(subscriptions)
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway state reconcile failed")
