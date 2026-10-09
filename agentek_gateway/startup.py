import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import APIRouter

from litellm._logging import verbose_proxy_logger

READY_POLL_INTERVAL_S = 0.2


class GatewayHost(Protocol):
    def include_router(self, router: APIRouter) -> None: ...

    def callbacks(self) -> list[object]: ...

    def has_database(self) -> bool: ...

    def has_router(self) -> bool: ...


CallbackFactory = Callable[[], object]
ReadyHandler = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class GatewayState:
    router_included: bool = False
    ready_task: asyncio.Task[None] | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True, slots=True)
class Plugin:
    callback_factories: Sequence[CallbackFactory] = ()
    on_ready: Sequence[ReadyHandler] = ()


GATEWAY_STATE = GatewayState()
DEFAULT_PLUGINS: Sequence[Plugin] = ()


def startup() -> None:
    """LITELLM_WORKER_STARTUP_HOOKS entry point: wires the plugin into the proxy of this worker."""
    from .api import build_api_router
    from .proxy_host import ProxyHost

    start_gateway(ProxyHost(), build_api_router, DEFAULT_PLUGINS, GATEWAY_STATE)


def start_gateway(
    host: GatewayHost,
    build_router: Callable[[], APIRouter],
    plugins: Sequence[Plugin],
    state: GatewayState,
) -> None:
    """Safe to call repeatedly in one process: the API router, callbacks and the readiness task are added once."""
    if not state.router_included:
        host.include_router(build_router())
        state.router_included = True
    register_callbacks(
        host.callbacks(),
        [factory for plugin in plugins for factory in plugin.callback_factories],
    )
    if state.ready_task is None:
        handlers = [handler for plugin in plugins for handler in plugin.on_ready]
        state.ready_task = asyncio.get_running_loop().create_task(
            _run_when_ready(host, handlers, state)
        )


def register_callbacks(
    registered: list[object], factories: Sequence[CallbackFactory]
) -> None:
    for factory in factories:
        if not any(type(existing) is factory for existing in registered):
            registered.append(factory())


def _log_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        verbose_proxy_logger.error(
            "agentek_gateway readiness task failed", exc_info=task.exception()
        )


async def wait_until_ready(
    host: GatewayHost, poll_interval_s: float = READY_POLL_INTERVAL_S
) -> None:
    while not (host.has_database() and host.has_router()):
        await asyncio.sleep(poll_interval_s)


async def _run_when_ready(
    host: GatewayHost, handlers: Sequence[ReadyHandler], state: GatewayState
) -> None:
    await wait_until_ready(host)
    for handler in handlers:
        await handler()
    state.ready.set()
    verbose_proxy_logger.info("agentek_gateway ready")
