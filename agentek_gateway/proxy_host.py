from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

import litellm

from .subscriptions.audit import AuditTable
from .subscriptions.credential_directory import DirectoryTable
from .subscriptions.credentials import CredentialTable
from .subscriptions.litellm_deployments import ModelTable, ProxyDb
from .subscriptions.prisma_repos import PolicyTable, SubscriptionTable
from .subscriptions.provider_settings import ConfigTable
from .subscriptions.state_db import StateTable

ShutdownHandler = Callable[[], Awaitable[None]]


def run_on_shutdown(app: FastAPI, handler: ShutdownHandler) -> None:
    """Runs the handler after the app's own shutdown, whatever the way the lifespan ended."""
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[object]:
        try:
            async with original(application) as state:
                yield state
        finally:
            await handler()

    app.router.lifespan_context = lifespan  # type: ignore[assignment]


class ProxyHost:
    """Binds the plugin to the running LiteLLM proxy; resolved lazily because the proxy module imports slowly."""

    def include_router(self, router: APIRouter) -> None:
        from litellm.proxy.proxy_server import app

        app.include_router(router)

    def on_shutdown(self, handler: ShutdownHandler) -> None:
        from litellm.proxy.proxy_server import app

        run_on_shutdown(app, handler)

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
