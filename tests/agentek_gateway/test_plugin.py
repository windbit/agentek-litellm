import asyncio

import fakeredis
from litellm.llms.chatgpt import authenticator
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.subscriptions.credentials import InMemoryCredentialStore
from agentek_gateway.subscriptions.memory import (
    InMemoryPolicyRepo,
    InMemorySubscriptionRepo,
)
from agentek_gateway.subscriptions.plugin import (
    ENV_REDIS_URL,
    Connections,
    build_proxy_runtime,
    default_plugins,
    redis_from_env,
)
from agentek_gateway.subscriptions.adapter import SubscriptionCallback
from agentek_gateway.subscriptions.refresh_guard import uninstall_refresh_guard
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

    def credentials_table(self) -> None:
        return None


async def test_runtime_is_built_loaded_and_observing_provider_errors() -> None:
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
        assert (
            runtime.parts.snapshot.current is not None,
            hasattr(ChatGPTResponsesAPIConfig, ORIGINAL_CLASS_ATTRIBUTE),
            authenticator.REFRESH_GUARD is not None,
        ) == (True, True, True)
    finally:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        uninstall_refresh_guard()
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


def memory_catalog(credentials, repo, models, directory=None):  # type: ignore[no-untyped-def]
    from agentek_gateway.subscriptions.audit import InMemoryAuditLog
    from agentek_gateway.subscriptions.catalog import Catalog
    from agentek_gateway.subscriptions.credential_runtime import NullCredentialRuntime
    from agentek_gateway.subscriptions.memory_catalog import (
        InMemoryCredentialDirectory,
        InMemoryProviderSettings,
        InMemorySubscriptionWriter,
    )
    from agentek_gateway.subscriptions.unit import Writes, fixed_unit

    directory = directory or InMemoryCredentialDirectory(credentials, "chatgpt")
    writer, settings, audit = (
        InMemorySubscriptionWriter(repo),
        InMemoryProviderSettings(),
        InMemoryAuditLog(),
    )
    return Catalog(
        directory=directory,
        writer=writer,
        models=models,
        audit=audit,
        settings=settings,
        unit=fixed_unit(Writes(writer, directory, settings, audit)),
        runtime=NullCredentialRuntime(),
    )


async def test_the_catalog_opens_the_operator_api_and_imports_credentials_at_start() -> (
    None
):
    from agentek_gateway.subscriptions.admin import ADMIN_SLOT
    from agentek_gateway.subscriptions.memory_catalog import InMemoryModelStore

    from .catalog_stack import auth_of, template_row

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    credentials, repo = InMemoryCredentialStore(), InMemorySubscriptionRepo()
    credentials.put("team-a", auth_of())
    models = InMemoryModelStore()
    models.add_template(template_row("m1"))
    connections = Connections(
        redis,
        fakeredis.FakeRedis(decode_responses=True),
        credentials,
        repo,
        InMemoryPolicyRepo(),
        memory_catalog(credentials, repo, models),
    )
    runtime = await build_proxy_runtime(FakeHost(), {}, FakeClock(), connections)  # type: ignore[arg-type]
    try:
        overview = await ADMIN_SLOT.admin.overview()  # type: ignore[union-attr]
        assert (
            [view.name for view in overview[1]],
            sorted(row for row in models.rows if row.startswith("sub:")),
        ) == (["team-a"], [f"sub:{overview[1][0].id}:m1"])
    finally:
        ADMIN_SLOT.admin = None
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        uninstall_refresh_guard()
        for task in tuple(runtime.parts.tasks._running):  # noqa: SLF001
            task.cancel()
        await asyncio.gather(
            *runtime.parts.tasks._running, return_exceptions=True
        )  # noqa: SLF001
        await redis.aclose()


async def test_the_operator_api_stays_closed_when_the_first_import_fails() -> None:
    import pytest

    from agentek_gateway.subscriptions.admin import ADMIN_SLOT
    from agentek_gateway.subscriptions.memory_catalog import (
        InMemoryCredentialDirectory,
        InMemoryModelStore,
    )

    class Broken(InMemoryCredentialDirectory):
        async def list_credentials(self, provider):  # type: ignore[no-untyped-def]
            raise RuntimeError("database down")

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    credentials, repo = InMemoryCredentialStore(), InMemorySubscriptionRepo()
    connections = Connections(
        redis,
        fakeredis.FakeRedis(decode_responses=True),
        credentials,
        repo,
        InMemoryPolicyRepo(),
        memory_catalog(
            credentials,
            repo,
            InMemoryModelStore(),
            Broken(credentials, "chatgpt"),
        ),
    )
    try:
        with pytest.raises(RuntimeError):
            await build_proxy_runtime(FakeHost(), {}, FakeClock(), connections)  # type: ignore[arg-type]
        assert ADMIN_SLOT.admin is None
    finally:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        uninstall_refresh_guard()
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task() and "Redis" in repr(task):
                task.cancel()
        await redis.aclose()


async def test_without_a_catalog_the_operator_api_stays_closed() -> None:
    from agentek_gateway.subscriptions.admin import ADMIN_SLOT

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
        assert ADMIN_SLOT.admin is None
    finally:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        uninstall_refresh_guard()
        for task in tuple(runtime.parts.tasks._running):  # noqa: SLF001
            task.cancel()
        await asyncio.gather(
            *runtime.parts.tasks._running, return_exceptions=True
        )  # noqa: SLF001
        await redis.aclose()
