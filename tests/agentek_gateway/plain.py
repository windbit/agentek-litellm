"""The plugin runtime without a Router or a mock backend: deployments are plain dicts fed to the filter."""

from dataclasses import dataclass

import fakeredis

from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.memory import (
    InMemoryPolicyRepo,
    InMemorySubscriptionRepo,
)
from agentek_gateway.subscriptions.model import Subscription
from agentek_gateway.subscriptions.ports import StateStore
from agentek_gateway.subscriptions.providers.chatgpt import ChatGPTProvider
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_slots import RedisSlotStore
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.runtime import (
    RuntimeDeps,
    SubscriptionRuntime,
    build_runtime,
)
from agentek_gateway.subscriptions.snapshot import SnapshotTiming
from agentek_gateway.subscriptions.state_db import InMemoryStateDb

from .conftest import FakeClock, make_subscription

MODEL = "gpt-x"
SHARED_ID = "deepseek-1"


def deployment(sub_id: str | None, model: str = MODEL) -> dict[str, object]:
    deployment_id = f"sub:{sub_id}:{model}" if sub_id else SHARED_ID
    return {
        "model_name": model,
        "litellm_params": {"model": f"chatgpt/{model}" if sub_id else "deepseek/x"},
        "model_info": {"id": deployment_id},
    }


@dataclass
class Plain:
    runtime: SubscriptionRuntime
    clock: FakeClock
    redis: fakeredis.FakeAsyncRedis
    server: fakeredis.FakeServer
    db: InMemoryStateDb
    store: RedisStateStore
    deployments: list[dict[str, object]]
    repo: InMemorySubscriptionRepo

    async def pick(self, request_id: str = "req", **request: object) -> list[str]:
        chosen = await self.runtime.gateway.filter(
            MODEL,
            self.deployments,
            {"metadata": {"agentek_request_id": request_id}, **request},
        )
        return [str(item["model_info"]["id"]) for item in chosen]  # type: ignore[index]


def plain_runtime(
    sub_ids: list[str],
    *,
    shared: bool = False,
    clock: FakeClock | None = None,
    server: fakeredis.FakeServer | None = None,
    db: InMemoryStateDb | None = None,
    timing: SnapshotTiming | None = None,
    subscriptions: list[Subscription] | None = None,
) -> Plain:
    clock = clock or FakeClock()
    server = server or fakeredis.FakeServer()
    redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    db = db or InMemoryStateDb()
    keys = Keys("t:")
    store = RedisStateStore(redis, db, clock, keys)
    subs = subscriptions or [make_subscription(sub_id) for sub_id in sub_ids]
    deployments = [deployment(sub_id) for sub_id in sub_ids]
    if shared:
        deployments.append(deployment(None))
    repo = InMemorySubscriptionRepo(subs)
    runtime = build_runtime(
        RuntimeDeps(
            clock=clock,
            config=GatewayConfig(),
            state_store=store,
            slot_store=RedisSlotStore(redis, clock, "t:"),
            repo=repo,
            policy=InMemoryPolicyRepo(),
            providers={"chatgpt": ChatGPTProvider(transport=None, probe_model=MODEL)},  # type: ignore[arg-type]
            model_list=lambda: deployments,
            timing=timing or SnapshotTiming(),
        )
    )
    return Plain(runtime, clock, redis, server, db, store, deployments, repo)
