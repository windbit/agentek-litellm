from collections.abc import Sequence

from fastapi import APIRouter

import litellm

from .subscriptions.state_db import StateTable


class ProxyHost:
    """Binds the plugin to the running LiteLLM proxy; resolved lazily because the proxy module imports slowly."""

    def include_router(self, router: APIRouter) -> None:
        from litellm.proxy.proxy_server import app

        app.include_router(router)

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
