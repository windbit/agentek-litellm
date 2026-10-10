"""In-memory catalog: credentials, subscriptions, deployments and operator services wired like the plugin wires them."""

from dataclasses import dataclass

import fakeredis

from agentek_gateway.subscriptions.audit import InMemoryAuditLog
from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.credentials import InMemoryCredentialStore
from agentek_gateway.subscriptions.importer import CredentialImporter
from agentek_gateway.subscriptions.memory import InMemorySubscriptionRepo
from agentek_gateway.subscriptions.memory_catalog import (
    InMemoryCredentialDirectory,
    InMemoryModelStore,
    InMemoryProviderSettings,
    InMemorySubscriptionWriter,
)
from agentek_gateway.subscriptions.model import Subscription
from agentek_gateway.subscriptions.model_copies import CopySync, ModelRow
from agentek_gateway.subscriptions.providers.chatgpt import ChatgptAuth
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.service import StateService
from agentek_gateway.subscriptions.state_db import InMemoryStateDb
from agentek_gateway.subscriptions.toggle import SubscriptionToggle

from .conftest import FakeClock

PROVIDER = "chatgpt"


def auth_of(suffix: str = "1", **fields: object) -> ChatgptAuth:
    return ChatgptAuth(
        access_token=f"access-secret-{suffix}",
        refresh_token=f"refresh-secret-{suffix}",
        id_token=f"id-secret-{suffix}",
        account_id="acct",
        expires_at=4_000_000_000.0,
        **fields,  # type: ignore[arg-type]
    )


def template_row(name: str, price: float = 1e-06) -> ModelRow:
    return ModelRow(
        model_id=f"template:{name}",
        model_name=name,
        litellm_params={"model": f"{PROVIDER}/{name}", "input_cost_per_token": price},
        model_info={"id": f"template:{name}", "mode": "responses"},
    )


@dataclass
class CatalogStack:
    clock: FakeClock
    redis: fakeredis.FakeAsyncRedis
    repo: InMemorySubscriptionRepo
    writer: InMemorySubscriptionWriter
    tokens: InMemoryCredentialStore
    directory: InMemoryCredentialDirectory
    models: InMemoryModelStore
    settings: InMemoryProviderSettings
    audit: InMemoryAuditLog
    keys: Keys
    store: RedisStateStore
    states: StateService
    toggle: SubscriptionToggle
    copies: CopySync
    importer: CredentialImporter


def catalog_stack(subscriptions: list[Subscription] | None = None) -> CatalogStack:
    clock = FakeClock()
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    keys = Keys("t:")
    store = RedisStateStore(redis, InMemoryStateDb(), clock, keys)
    repo = InMemorySubscriptionRepo(subscriptions or [])
    writer = InMemorySubscriptionWriter(repo)
    tokens = InMemoryCredentialStore()
    directory = InMemoryCredentialDirectory(tokens, PROVIDER)
    models = InMemoryModelStore()
    settings = InMemoryProviderSettings()
    audit = InMemoryAuditLog()
    states = StateService(store, clock, GatewayConfig())
    toggle = SubscriptionToggle(store, repo, states)
    copies = CopySync(models, repo)
    return CatalogStack(
        clock=clock,
        redis=redis,
        repo=repo,
        writer=writer,
        tokens=tokens,
        directory=directory,
        models=models,
        settings=settings,
        audit=audit,
        keys=keys,
        store=store,
        states=states,
        toggle=toggle,
        copies=copies,
        importer=CredentialImporter(directory, repo, writer, toggle, audit, models),
    )
