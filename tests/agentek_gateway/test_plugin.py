import asyncio

import fakeredis
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.subscriptions.plugin import (
    ENV_REDIS_URL,
    build_proxy_runtime,
    default_plugins,
    redis_from_env,
)
from agentek_gateway.subscriptions.adapter import SubscriptionCallback
from agentek_gateway.subscriptions.providers.observer import (
    ORIGINAL_CLASS_ATTRIBUTE,
    uninstall_error_observer,
)

from .conftest import FakeClock
from .test_state_db import FakeTable


class FakeHost:
    def __init__(self) -> None:
        self.table = FakeTable()

    def model_list(self) -> list[dict[str, object]]:
        return []

    def state_table(self) -> FakeTable:
        return self.table


async def test_runtime_is_built_loaded_and_observing_provider_errors() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    runtime = await build_proxy_runtime(FakeHost(), {}, FakeClock(), redis)  # type: ignore[arg-type]
    try:
        assert (
            runtime.parts.snapshot.current is not None,
            hasattr(ChatGPTResponsesAPIConfig, ORIGINAL_CLASS_ATTRIBUTE),
        ) == (True, True)
    finally:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        for task in tuple(runtime.parts.tasks._running):  # noqa: SLF001
            task.cancel()
        await asyncio.gather(
            *runtime.parts.tasks._running, return_exceptions=True
        )  # noqa: SLF001
        await redis.aclose()


def test_redis_url_wins_over_host_settings() -> None:
    client = redis_from_env(
        {ENV_REDIS_URL: "redis://cache.internal:6400/3", "REDIS_HOST": "other"}
    )
    kwargs = client.connection_pool.connection_kwargs

    assert (kwargs["host"], kwargs["port"], kwargs["db"]) == ("cache.internal", 6400, 3)


def test_redis_host_settings_are_used_without_a_url() -> None:
    client = redis_from_env({"REDIS_HOST": "cache.internal", "REDIS_PORT": "6401"})
    kwargs = client.connection_pool.connection_kwargs

    assert (kwargs["host"], kwargs["port"]) == ("cache.internal", 6401)


def test_default_plugin_registers_the_subscription_callback_once() -> None:
    (plugin,) = default_plugins()

    assert plugin.callback_factories == (SubscriptionCallback,)
