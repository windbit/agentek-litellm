"""Background loops and the admin API are wired and survive one bad iteration."""

import asyncio
import time

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.subscriptions import prometheus_telemetry
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
from agentek_gateway.subscriptions.snapshot import SnapshotTiming

from .conftest import FakeClock
from .plain import plain_runtime
from .test_plugin import FakeHost

LOOP_COROUTINES = {
    "SnapshotCache.run",
    "RedisListener.run",
    "_reconcile_forever",
    "LeaderDuties.run",
    "TelemetryLoop.run",
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
            for task in runtime.parts.tasks._running  # noqa: SLF001
        }
    finally:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        uninstall_refresh_guard()
        await runtime.parts.tasks.cancel_all()
        await redis.aclose()

    assert started == LOOP_COROUTINES


async def test_snapshot_loop_keeps_running_after_one_failed_refresh() -> None:
    plain = plain_runtime(
        ["a", "b"], timing=SnapshotTiming(interval_s=FAST_INTERVAL_S, directory_ttl_s=0)
    )
    real_list = plain.repo.list_subscriptions
    failures = {"left": 1}

    async def flaky_list():  # noqa: ANN202
        if failures["left"]:
            failures["left"] -= 1
            raise ConnectionError("database blip")
        return await real_list()

    plain.repo.list_subscriptions = flaky_list  # type: ignore[method-assign]
    snapshot = plain.runtime.parts.snapshot
    runner = asyncio.get_running_loop().create_task(snapshot.run())
    try:
        started = time.monotonic()
        while snapshot.current is None and time.monotonic() - started < 2:
            await asyncio.sleep(FAST_INTERVAL_S)
        loaded = snapshot.current is not None
    finally:
        runner.cancel()

    assert (failures["left"], loaded) == (0, True)


async def test_metrics_loop_keeps_running_after_one_failed_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prometheus_telemetry, "PUBLISH_INTERVAL_S", FAST_INTERVAL_S)
    plain = plain_runtime(["a"])
    loop = TelemetryLoop(
        plain.runtime.parts.snapshot,
        plain.store,
        plain.runtime.parts.egress,
        PrometheusTelemetry(),
        plain.clock,
    )
    calls: list[int] = []

    async def flaky_publish() -> None:
        calls.append(len(calls))
        if len(calls) == 1:
            raise ConnectionError("redis blip")

    loop.publish_once = flaky_publish  # type: ignore[method-assign]
    runner = asyncio.get_running_loop().create_task(loop.run())
    try:
        started = time.monotonic()
        while len(calls) < 3 and time.monotonic() - started < 2:
            await asyncio.sleep(FAST_INTERVAL_S)
    finally:
        runner.cancel()

    assert len(calls) >= 3
