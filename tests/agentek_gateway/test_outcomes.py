from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
from agentek_gateway.subscriptions.model import SubscriptionState as S
from agentek_gateway.subscriptions.providers.base import LimitReached
from agentek_gateway.subscriptions.events import LimitWindow
from agentek_gateway.subscriptions.providers.observer import (
    AttemptContext,
    ObservedFailure,
)

from .stack import MODEL, Stack, running_stack
from .test_adapter_hooks import attempt_kwargs


async def begin_attempt(stack: Stack) -> dict[str, object]:
    kwargs = attempt_kwargs()
    await stack.callback.async_pre_call_deployment_hook(kwargs, None)
    return kwargs


async def test_observed_failure_frees_the_slot_of_the_failed_attempt_at_once() -> None:
    async with running_stack(["a"]) as stack:
        await begin_attempt(stack)
        context = AttemptContext("r1", 1, "a", f"sub:a:{MODEL}", 0)
        failure = ObservedFailure(
            context, 429, {}, "", LimitReached(LimitWindow.WEEKLY, None)
        )

        stack.runtime.outcomes.on_observed(failure)
        await stack.settle()

        record = await stack.store.read_state("a")
        assert (
            (await stack.slot_store.in_flight(["a"]))["a"],
            record.state if record else None,
        ) == (0, S.RATE_LIMITED)


async def test_own_error_of_the_plugin_is_not_counted_against_the_last_attempt() -> (
    None
):
    async with running_stack(["a"]) as stack:
        kwargs = await begin_attempt(stack)
        own = NoAvailableSubscriptionsError(MODEL, None, 10)

        await stack.callback.async_log_failure_event(
            {**kwargs, "exception": own, "litellm_params": kwargs}, None, None, None  # type: ignore[arg-type]
        )

        counts = await stack.store.unclassified_counts(["a"], 120.0)
        assert dict(counts) == {"a": 0}


async def test_connection_error_without_a_reply_is_counted_once_per_attempt() -> None:
    async with running_stack(["a"]) as stack:
        kwargs = await begin_attempt(stack)
        event = {
            **kwargs,
            "exception": ConnectionError("boom"),
            "litellm_params": kwargs,
        }

        for _ in range(2):
            await stack.callback.async_log_failure_event(event, None, None, None)  # type: ignore[arg-type]

        counts = await stack.store.unclassified_counts(["a"], 120.0)
        assert dict(counts) == {"a": 1}


class UpstreamError(Exception):
    def __init__(self, status_code: int, text: str) -> None:
        super().__init__(text)
        self.status_code = status_code


async def failing_stream(error: Exception):  # type: ignore[no-untyped-def]
    yield "first"
    raise error


async def drain_stream(stack: Stack, error: Exception) -> None:
    await begin_attempt(stack)
    watched = stack.runtime.outcomes.watch_stream(
        {"metadata": {"agentek_request_id": "r1"}}, failing_stream(error)
    )
    try:
        async for _ in watched:
            pass
    except UpstreamError:
        pass
    await stack.finished()


async def test_server_error_after_the_first_chunk_is_not_counted() -> None:
    async with running_stack(["a"]) as stack:
        await drain_stream(stack, UpstreamError(503, "overloaded"))

        counts = await stack.store.unclassified_counts(["a"], 120.0)
        assert dict(counts) == {"a": 0}


async def test_limit_error_after_the_first_chunk_blocks_the_subscription() -> None:
    body = '{"error": {"type": "usage_limit_reached", "resets_in_seconds": 600}}'
    async with running_stack(["a"]) as stack:
        await drain_stream(stack, UpstreamError(429, body))

        record = await stack.store.read_state("a")
        assert record is not None and record.state is S.RATE_LIMITED
