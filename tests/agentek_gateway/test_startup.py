import asyncio

from fastapi import APIRouter

from agentek_gateway.metrics import get_or_create_metric
from agentek_gateway.startup import (
    GatewayState,
    Plugin,
    register_callbacks,
    start_gateway,
    wait_until_ready,
)


class FakeHost:
    def __init__(self, *, database_after: int = 0) -> None:
        self.routers: list[APIRouter] = []
        self.registered: list[object] = []
        self.polls = 0
        self.database_after = database_after

    def include_router(self, router: APIRouter) -> None:
        self.routers.append(router)

    def callbacks(self) -> list[object]:
        return self.registered

    def has_database(self) -> bool:
        self.polls += 1
        return self.polls > self.database_after

    def has_router(self) -> bool:
        return True


class Marker:
    pass


async def run(host: FakeHost, state: GatewayState, handlers=()) -> None:  # type: ignore[no-untyped-def]
    plugin = Plugin(callback_factories=(Marker,), on_ready=tuple(handlers))
    start_gateway(host, APIRouter, [plugin], state)
    assert state.ready_task is not None
    await state.ready_task


async def test_second_startup_adds_nothing() -> None:
    host, state = FakeHost(), GatewayState()

    await run(host, state)
    await run(host, state)

    assert (len(host.routers), len(host.registered)) == (1, 1)


async def test_ready_handlers_run_once_however_often_startup_is_called() -> None:
    host, state, calls = FakeHost(), GatewayState(), []

    async def handler() -> None:
        calls.append(1)

    await run(host, state, [handler])
    await run(host, state, [handler])

    assert (len(calls), state.ready.is_set()) == (1, True)


async def test_ready_handlers_wait_for_the_database() -> None:
    host, state, order = FakeHost(database_after=3), GatewayState(), []

    async def handler() -> None:
        order.append(host.polls)

    await run(host, state, [handler])

    assert order[0] > 3


async def test_wait_until_ready_polls_until_both_exist() -> None:
    host = FakeHost(database_after=2)

    await asyncio.wait_for(wait_until_ready(host, poll_interval_s=0.001), 2)

    assert host.polls == 3


def test_callback_of_the_same_type_is_not_registered_twice() -> None:
    registered: list[object] = [Marker()]

    register_callbacks(registered, [Marker, Marker])

    assert len(registered) == 1


def test_metric_registration_is_idempotent() -> None:
    first = get_or_create_metric("counter", "agentek_test_idem", "doc")
    second = get_or_create_metric("counter", "agentek_test_idem", "doc")
    gauge = get_or_create_metric("gauge", "agentek_test_idem_gauge", "doc", ["l"])

    assert (
        first is second,
        get_or_create_metric("gauge", "agentek_test_idem_gauge", "doc", ["l"]) is gauge,
    ) == (True, True)


async def test_failing_ready_handler_is_logged_and_leaves_the_gateway_not_ready(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    host, state = FakeHost(), GatewayState()

    async def broken() -> None:
        raise RuntimeError("redis unreachable")

    with caplog.at_level(logging.ERROR):
        try:
            await run(host, state, [broken])
        except RuntimeError:
            pass
        await asyncio.sleep(0)

    assert (state.ready.is_set(), "readiness task failed" in caplog.text) == (
        False,
        True,
    )
