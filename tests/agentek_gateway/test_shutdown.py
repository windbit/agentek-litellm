import asyncio
import inspect

from agentek_gateway.proxy_host import run_before_proxy_shutdown
from agentek_gateway.subscriptions.tasks import BackgroundTasks


class FakeProxy:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def proxy_shutdown_event(self) -> None:
        self.order.append("proxy shutdown")


async def test_handler_runs_before_the_proxys_own_shutdown() -> None:
    order: list[str] = []
    proxy = FakeProxy(order)

    async def handler() -> None:
        order.append("handler")

    run_before_proxy_shutdown(proxy, handler)
    await proxy.proxy_shutdown_event()

    assert order == ["handler", "proxy shutdown"]


async def test_proxy_shutdown_still_runs_when_the_handler_fails() -> None:
    order: list[str] = []
    proxy = FakeProxy(order)

    async def handler() -> None:
        raise RuntimeError("flush failed")

    run_before_proxy_shutdown(proxy, handler)
    await proxy.proxy_shutdown_event()

    assert order == ["proxy shutdown"]


def test_the_proxy_calls_the_function_we_wrap_by_global_name() -> None:
    from litellm.proxy import proxy_server

    assert inspect.iscoroutinefunction(proxy_server.proxy_shutdown_event)
    assert "await proxy_shutdown_event()" in inspect.getsource(
        proxy_server.proxy_startup_event
    )


async def test_drain_waits_for_work_that_finishes_in_time() -> None:
    tasks = BackgroundTasks()
    done: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0.05)
        done.append("written")

    tasks.spawn(work())
    await tasks.drain_within(1.0)

    assert done == ["written"]


async def test_drain_cancels_work_that_outlives_the_timeout() -> None:
    tasks = BackgroundTasks()
    tasks.spawn(asyncio.sleep(30))

    await tasks.drain_within(0.05)

    assert tasks.running == ()
