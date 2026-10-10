"""Background loops and the admin API are wired and survive one bad iteration."""

import asyncio
import time

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.api import build_api_router
from agentek_gateway.subscriptions.credentials import InMemoryCredentialStore
from agentek_gateway.subscriptions.memory import (
    InMemoryPolicyRepo,
    InMemorySubscriptionRepo,
)
from agentek_gateway.subscriptions.plugin import Connections, build_proxy_runtime
from agentek_gateway.subscriptions.prometheus_telemetry import (
    PrometheusTelemetry,
    TelemetryLoop,
)
from agentek_gateway.subscriptions.providers.observer import uninstall_error_observer
from agentek_gateway.subscriptions.refresh_guard import uninstall_refresh_guard
from agentek_gateway.subscriptions.model import Subscription
from agentek_gateway.subscriptions.snapshot import SnapshotTiming

from .conftest import FakeClock, make_subscription
from .plain import plain_runtime
from .test_plugin import FakeHost

LOOP_COROUTINES = {
    "SnapshotCache.run",
    "RedisListener.run",
    "_reconcile_forever",
    "LeaderDuties.run",
    "TelemetryLoop.run",
    "CredentialPairsLoop.run",
    "StatsLoop.run",
}
FAST_INTERVAL_S = 0.02


def api_client(role: LitellmUserRoles | None) -> TestClient:
    app = FastAPI()
    app.include_router(build_api_router())
    if role is not None:
        app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(
            user_role=role
        )
    return TestClient(app)


def test_status_endpoint_is_open_to_the_proxy_admin() -> None:
    response = api_client(LitellmUserRoles.PROXY_ADMIN).get("/agentek/status")

    assert (response.status_code, response.json()["plugin"]) == (200, "agentek_gateway")


def test_status_endpoint_refuses_a_key_that_is_not_the_proxy_admin() -> None:
    response = api_client(LitellmUserRoles.INTERNAL_USER).get("/agentek/status")

    assert response.status_code == 403


async def test_runtime_starts_every_background_loop() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    connections = Connections(
        redis,
        fakeredis.FakeRedis(decode_responses=True),
        InMemoryCredentialStore(),
        InMemorySubscriptionRepo(),
        InMemoryPolicyRepo(),
    )
    runtime = await build_proxy_runtime(FakeHost(), {}, FakeClock(), connections)  # type: ignore[arg-type]
    try:
        started = {
            task.get_coro().__qualname__  # type: ignore[union-attr]
            for task in runtime.parts.tasks.running
        }
    finally:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        uninstall_refresh_guard()
        await runtime.parts.tasks.cancel_all()
        await redis.aclose()

    assert started == LOOP_COROUTINES


class FlakyOnceRepo(InMemorySubscriptionRepo):
    def __init__(self, subscriptions: list[Subscription]) -> None:
        super().__init__(subscriptions)
        self.failures_left = 1

    async def list_subscriptions(self) -> list[Subscription]:
        if self.failures_left:
            self.failures_left -= 1
            raise ConnectionError("database blip")
        return await super().list_subscriptions()


class FlakyOncePublisher(TelemetryLoop):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.calls = 0

    async def publish_once(self) -> None:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("redis blip")


async def test_snapshot_loop_keeps_running_after_one_failed_refresh() -> None:
    repo = FlakyOnceRepo([make_subscription("a"), make_subscription("b")])
    plain = plain_runtime(
        ["a", "b"],
        timing=SnapshotTiming(interval_s=FAST_INTERVAL_S, directory_ttl_s=0),
        repo=repo,
    )
    snapshot = plain.runtime.parts.snapshot
    runner = asyncio.get_running_loop().create_task(snapshot.run())
    try:
        started = time.monotonic()
        while snapshot.current is None and time.monotonic() - started < 2:
            await asyncio.sleep(FAST_INTERVAL_S)
        loaded = snapshot.current is not None
    finally:
        runner.cancel()

    assert (repo.failures_left, loaded) == (0, True)


async def test_metrics_loop_keeps_running_after_one_failed_publish() -> None:
    plain = plain_runtime(["a"])
    loop = FlakyOncePublisher(
        plain.runtime.parts.snapshot,
        plain.store,
        plain.runtime.parts.egress,
        PrometheusTelemetry(),
        plain.clock,
        interval_s=FAST_INTERVAL_S,
    )
    runner = asyncio.get_running_loop().create_task(loop.run())
    try:
        started = time.monotonic()
        while loop.calls < 3 and time.monotonic() - started < 2:
            await asyncio.sleep(FAST_INTERVAL_S)
    finally:
        runner.cancel()

    assert loop.calls >= 3
