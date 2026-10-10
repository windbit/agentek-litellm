from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from fastapi import APIRouter

import litellm
from litellm._logging import verbose_proxy_logger

from .subscriptions.audit import AuditTable
from .subscriptions.credential_directory import DirectoryTable
from .subscriptions.credentials import CredentialTable
from .subscriptions.litellm_deployments import ModelTable, ProxyDb
from .subscriptions.prisma_repos import PolicyTable, SubscriptionTable
from .subscriptions.provider_settings import ConfigTable
from .subscriptions.state_db import StateTable

ShutdownHandler = Callable[[], Awaitable[None]]


class ShutdownTarget(Protocol):
    proxy_shutdown_event: Callable[[], Awaitable[None]]


def run_before_proxy_shutdown(target: ShutdownTarget, handler: ShutdownHandler) -> None:
    """Runs the handler first in the proxy's own shutdown, while Prisma is still connected.

    The proxy calls its shutdown function by global name from inside the lifespan that is already running when
    plugins start, so wrapping the lifespan is too late; replacing the function is not.
    """
    original = target.proxy_shutdown_event

    async def shutdown() -> None:
        try:
            await handler()
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway shutdown handler failed")
        await original()

    target.proxy_shutdown_event = shutdown


class ProxyHost:
    """Binds the plugin to the running LiteLLM proxy; resolved lazily because the proxy module imports slowly."""

    def include_router(self, router: APIRouter) -> None:
        from litellm.proxy.proxy_server import app

        app.include_router(router)

    def on_shutdown(self, handler: ShutdownHandler) -> None:
        from litellm.proxy import proxy_server

        run_before_proxy_shutdown(proxy_server, handler)  # type: ignore[arg-type]

    def callbacks(self) -> list[object]:
        return litellm.callbacks

    def has_database(self) -> bool:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client is not None

    def has_router(self) -> bool:
        from litellm.proxy import proxy_server

        return proxy_server.llm_router is not None

    def model_list(self) -> Sequence[dict[str, object]]:
        from litellm.proxy import proxy_server

        router = proxy_server.llm_router
        return router.model_list if router is not None else []

    def state_table(self) -> StateTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_agenteksubscriptionstate  # type: ignore[union-attr,return-value]

    def credentials_table(self) -> CredentialTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_credentialstable  # type: ignore[union-attr,return-value]

    def subscription_table(self) -> SubscriptionTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_agenteksubscription  # type: ignore[union-attr,return-value]

    def policy_table(self) -> PolicyTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_agenteksubscriptionpolicy  # type: ignore[union-attr,return-value]

    def directory_table(self) -> DirectoryTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_credentialstable  # type: ignore[union-attr,return-value]

    def model_table(self) -> ModelTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_proxymodeltable  # type: ignore[union-attr,return-value]

    def audit_table(self) -> AuditTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_agentekaudit  # type: ignore[union-attr,return-value]

    def config_table(self) -> ConfigTable:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client.db.litellm_config  # type: ignore[union-attr,return-value]

    def proxy_db(self) -> ProxyDb:
        from litellm.proxy import proxy_server

        return proxy_server.prisma_client  # type: ignore[return-value]
