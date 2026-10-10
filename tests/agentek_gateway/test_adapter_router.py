import litellm
import pytest
from litellm.integrations.custom_logger import CustomLogger

from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
from agentek_gateway.subscriptions.model import SubscriptionState as S

from agentek_gateway.subscriptions.redis_state import RedisStateStore

from .stack import MODEL, Stack, account_of, running_stack


class Recorder(CustomLogger):
    def __init__(self) -> None:
        super().__init__()
        self.successes: list[dict[str, object]] = []

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # type: ignore[no-untyped-def]
        self.successes.append(kwargs)


async def state_of(stack: Stack, sub_id: str) -> S | None:
    record = await stack.store.read_state(sub_id)
    return record.state if record else None


async def in_flight(stack: Stack) -> dict[str, int]:
    return dict(await stack.slot_store.in_flight(list(stack.subscriptions)))


async def test_success_goes_to_the_first_subscription() -> None:
    async with running_stack(["a", "b"]) as stack:
        response = await stack.call()

        assert (
            response.choices[0].message.content,  # type: ignore[attr-defined]
            stack.mock.accounts_served(),
            await in_flight(stack),
        ) == ("Hello from mock", [account_of("a")], {"a": 0, "b": 0})


async def test_usage_limit_moves_the_request_to_the_next_subscription_without_error() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "usage_limit")

        response = await stack.call()

        assert (
            response.choices[0].message.content,  # type: ignore[attr-defined]
            stack.mock.accounts_served(),
            await state_of(stack, "a"),
            stack.switches.events,
            await in_flight(stack),
        ) == (
            "Hello from mock",
            [account_of("a"), account_of("b")],
            S.RATE_LIMITED,
            [("a", "limit")],
            {"a": 0, "b": 0},
        )


async def test_every_subscription_exhausted_by_upstream_429_ends_the_request_with_the_plugin_429() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        for sub_id in ("a", "b"):
            stack.mock.script(account_of(sub_id), "usage_limit")

        with pytest.raises(NoAvailableSubscriptionsError) as raised:
            await stack.call()

        assert (
            raised.value.status_code,
            raised.value.headers,
            stack.mock.accounts_served(),
        ) == (429, {"retry-after": "10"}, [account_of("a"), account_of("b")])


async def test_new_request_with_every_deployment_in_router_cooldown_gets_the_plugin_429() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        for sub_id in ("a", "b"):
            stack.mock.script(account_of(sub_id), "usage_limit")
        with pytest.raises(NoAvailableSubscriptionsError):
            await stack.call()
        served_before = len(stack.mock.received)

        with pytest.raises(NoAvailableSubscriptionsError) as raised:
            await stack.call()

        assert (
            raised.value.status_code,
            raised.value.headers,
            len(stack.mock.received) - served_before,
        ) == (429, {"retry-after": "10"}, 0)


async def test_switching_subscriptions_leaves_the_credential_tag_of_the_last_one_only() -> (
    None
):
    recorder = Recorder()
    async with running_stack(["a", "b"]) as stack:
        litellm.callbacks.append(recorder)
        stack.mock.script(account_of("a"), "usage_limit")

        await stack.call()

        tags = recorder.successes[-1]["standard_logging_object"]["request_tags"]  # type: ignore[index]
        assert [tag for tag in tags if tag.startswith("Credential: ")] == [
            "Credential: cred-b"
        ]


async def test_one_unclassified_failure_is_counted_once() -> None:
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "overloaded")

        await stack.call()

        counts = await stack.store.unclassified_counts(["a", "b"], 120.0)
        assert dict(counts) == {"a": 1, "b": 0}


async def test_busy_subscription_is_skipped_without_penalty() -> None:
    async with running_stack(["a", "b"], slot_limit=1) as stack:
        assert await stack.slot_store.reserve("a", "other-request", 1, 900.0)

        response = await stack.call()

        assert (
            response.choices[0].message.content,  # type: ignore[attr-defined]
            stack.mock.accounts_served(),
            await state_of(stack, "a"),
            stack.switches.events,
        ) == ("Hello from mock", [account_of("b")], None, [("a", "busy")])


async def test_every_subscription_busy_gives_the_plugin_429() -> None:
    async with running_stack(["a", "b"], slot_limit=1) as stack:
        for sub_id in ("a", "b"):
            assert await stack.slot_store.reserve(sub_id, "other", 1, 900.0)

        with pytest.raises(NoAvailableSubscriptionsError) as raised:
            await stack.call()

        assert (raised.value.status_code, stack.mock.accounts_served()) == (429, [])


async def test_stream_finished_by_the_client_releases_the_slot() -> None:
    async with running_stack(["a", "b"]) as stack:
        stream = await stack.open_stream()
        first = await anext(stream)
        assert first is not None
        assert (await in_flight(stack))["a"] == 1

        await stream.aclose()
        await stack.finished()

        assert await in_flight(stack) == {"a": 0, "b": 0}


async def test_stream_that_completes_releases_the_slot_and_keeps_the_state() -> None:
    async with running_stack(["a", "b"]) as stack:
        stream = await stack.open_stream()

        chunks = [chunk async for chunk in stream]
        await stack.finished()

        assert (
            len(chunks) > 1,
            await in_flight(stack),
            await state_of(stack, "a"),
        ) == (True, {"a": 0, "b": 0}, None)


async def test_response_failed_inside_the_stream_blocks_the_subscription() -> None:
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "sse_failed")
        stream = await stack.open_stream()

        _ = [chunk async for chunk in stream]
        await stack.finished()

        assert (await state_of(stack, "a"), await in_flight(stack)) == (
            S.RATE_LIMITED,
            {"a": 0, "b": 0},
        )


async def test_nonstreaming_success_near_the_limit_marks_the_subscription_soft_limited() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "ok_high")

        await stack.call()

        assert await state_of(stack, "a") == S.SOFT_LIMITED


async def test_stream_headers_near_the_limit_mark_the_subscription_soft_limited() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "ok_high")
        stream = await stack.open_stream()

        _ = [chunk async for chunk in stream]
        await stack.finished()

        assert await state_of(stack, "a") == S.SOFT_LIMITED


async def test_success_without_limit_headers_keeps_the_soft_limit() -> None:
    async with running_stack(["a"]) as stack:
        stack.mock.script(account_of("a"), "ok_high", "ok_nolimits")
        await stack.call()

        await stack.call()

        assert await state_of(stack, "a") == S.SOFT_LIMITED


async def test_upstream_cut_after_the_first_chunk_is_not_an_error_of_the_subscription() -> (
    None
):
    async with running_stack(["a"]) as stack:
        stack.mock.script(account_of("a"), "midstream_abort")
        stream = await stack.open_stream()

        with pytest.raises(Exception):  # noqa: B017, PT011
            _ = [chunk async for chunk in stream]
        await stack.finished()

        counts = await stack.store.unclassified_counts(["a"], 120.0)
        assert (dict(counts), await in_flight(stack)) == ({"a": 0}, {"a": 0})


async def test_cancelled_stream_task_frees_the_slot() -> None:
    import asyncio

    async with running_stack(["a"]) as stack:
        stack.mock.script(account_of("a"), "slow_stream")
        stream = await stack.open_stream()
        started = asyncio.Event()

        async def consume() -> None:
            async for _ in stream:
                started.set()

        task = asyncio.get_running_loop().create_task(consume())
        await started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await stack.finished()

        assert await in_flight(stack) == {"a": 0}


async def test_unauthorized_reply_starts_a_token_refresh_and_the_request_moves_on() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "unauthorized")

        response = await stack.call()

        assert (
            response.choices[0].message.content,  # type: ignore[attr-defined]
            await state_of(stack, "a"),
            stack.switches.events,
        ) == ("Hello from mock", S.AUTH_REFRESHING, [("a", "auth")])


async def test_deactivated_account_is_banned_and_the_request_moves_on() -> None:
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "deactivated")

        await stack.call()

        assert (await state_of(stack, "a"), stack.mock.accounts_served()) == (
            S.BANNED,
            [account_of("a"), account_of("b")],
        )


async def test_model_the_account_does_not_support_is_remembered_for_later_requests() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "model_not_supported")

        first = await stack.call()
        await stack.refresh()
        await stack.call()

        assert (
            first.choices[0].message.content,  # type: ignore[attr-defined]
            stack.mock.accounts_served(),
            await state_of(stack, "a"),
        ) == (
            "Hello from mock",
            [account_of("a"), account_of("b"), account_of("b")],
            None,
        )


async def test_model_not_supported_is_remembered_for_a_day_then_the_subscription_is_tried_again() -> (
    None
):
    day_s = 24 * 3600
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "model_not_supported")
        stack.mock.script(account_of("b"), default="ok_nolimits")
        await stack.call()
        await stack.refresh()

        stack.clock.advance(day_s - 60)
        await stack.refresh()
        await stack.call()
        within_the_day = stack.mock.accounts_served()[2:]
        stack.clock.advance(120)
        await stack.refresh()
        await stack.call()

        assert (within_the_day, stack.mock.accounts_served()[3]) == (
            [account_of("b")],
            account_of("a"),
        )


async def test_model_not_supported_on_the_last_candidate_reaches_the_client_as_400() -> (
    None
):
    async with running_stack(["a"]) as stack:
        stack.mock.script(account_of("a"), "model_not_supported")

        with pytest.raises(litellm.BadRequestError):
            await stack.call()


async def test_401_right_after_a_token_refresh_does_not_start_another_refresh() -> None:
    async with running_stack(["a", "b"]) as stack:
        await stack.store.mark_refreshed("cred-a", 60)
        stack.mock.script(account_of("a"), "unauthorized")

        response = await stack.call()

        assert (
            response.choices[0].message.content,  # type: ignore[attr-defined]
            await state_of(stack, "a"),
            stack.mock.accounts_served(),
        ) == ("Hello from mock", None, [account_of("a"), account_of("b")])


async def test_successful_chat_completion_records_the_limit_windows_of_its_subscription() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        await stack.call()

        usage = await stack.store.read_all_usage()

        assert {
            sub_id: (record.limits.five_hour.used_percent, record.limits.weekly.used_percent)  # type: ignore[union-attr]
            for sub_id, record in usage.items()
        } == {"a": (12.0, 31.0)}


async def test_successful_responses_request_records_the_limit_windows_of_its_subscription() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        await stack.respond()

        assert set(await stack.store.read_all_usage()) == {"a"}


class UnwritableState(RedisStateStore):
    """Redis answers reads but every state write times out, as with a starved event loop."""

    async def compare_and_set_state(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise TimeoutError("Timeout reading from redis")


async def test_dead_subscription_is_not_tried_again_while_its_state_cannot_be_written() -> (
    None
):
    async with running_stack(["a", "b"], store_class=UnwritableState) as stack:
        stack.mock.script(account_of("a"), default="usage_limit")

        for _ in range(6):
            await stack.call()

        assert stack.mock.accounts_served().count(account_of("a")) == 1


async def test_ten_dead_subscriptions_of_twelve_never_show_a_429_to_the_client_from_a_cold_start() -> (
    None
):
    sub_ids = [f"s{index:02d}" for index in range(12)]
    async with running_stack(sub_ids) as stack:
        for sub_id in sub_ids[:10]:
            stack.mock.script(account_of(sub_id), default="usage_limit")

        answers = [await stack.call() for _ in range(3)]

        assert [
            answer.choices[0].message.content  # type: ignore[attr-defined]
            for answer in answers
        ] == ["Hello from mock"] * 3


async def test_flushed_redis_does_not_send_a_request_to_dead_subscriptions_into_a_429() -> (
    None
):
    sub_ids = [f"s{index:02d}" for index in range(12)]
    async with running_stack(sub_ids) as stack:
        for sub_id in sub_ids[:10]:
            stack.mock.script(account_of(sub_id), default="usage_limit")
        await stack.call()
        await stack.redis.flushall()
        stack.mock.received.clear()

        answer = await stack.call()

        assert answer.choices[0].message.content == "Hello from mock"  # type: ignore[attr-defined]


async def test_server_errors_on_every_subscription_keep_the_routers_own_retry_count() -> (
    None
):
    sub_ids = [f"s{index:02d}" for index in range(12)]
    async with running_stack(sub_ids) as stack:
        for sub_id in sub_ids:
            stack.mock.script(account_of(sub_id), default="overloaded")

        with pytest.raises(Exception):
            await stack.call()

        assert len(stack.mock.received) == 5


async def test_client_asking_for_no_retries_still_gets_past_dead_subscriptions() -> (
    None
):
    sub_ids = [f"s{index:02d}" for index in range(12)]
    async with running_stack(sub_ids) as stack:
        for sub_id in sub_ids[:10]:
            stack.mock.script(account_of(sub_id), default="usage_limit")

        answer = await stack.call(num_retries=0)

        assert answer.choices[0].message.content == "Hello from mock"  # type: ignore[attr-defined]


async def test_responses_path_gets_past_dead_subscriptions_too() -> None:
    sub_ids = [f"s{index:02d}" for index in range(12)]
    async with running_stack(sub_ids) as stack:
        for sub_id in sub_ids[:10]:
            stack.mock.script(account_of(sub_id), default="usage_limit")

        answer = await stack.respond()

        assert answer is not None
