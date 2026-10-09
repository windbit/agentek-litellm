from fastapi import APIRouter

import litellm


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
