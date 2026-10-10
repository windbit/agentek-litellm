import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI

from agentek_gateway.proxy_host import run_on_shutdown
from agentek_gateway.subscriptions.tasks import BackgroundTasks


async def test_handler_runs_after_the_apps_own_shutdown() -> None:
    order: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        order.append("start")
        yield
        order.append("app shutdown")

    app = FastAPI(lifespan=lifespan)

    async def handler() -> None:
        order.append("handler")

    run_on_shutdown(app, handler)
    async with app.router.lifespan_context(app):
        order.append("serving")

    assert order == ["start", "serving", "app shutdown", "handler"]


async def test_handler_runs_when_the_apps_shutdown_fails() -> None:
    ran = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        yield
        raise RuntimeError("teardown failed")

    app = FastAPI(lifespan=lifespan)

    async def handler() -> None:
        ran.set()

    run_on_shutdown(app, handler)
    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app):
            pass

    assert ran.is_set()


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
