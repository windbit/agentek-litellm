import litellm
import pytest

from .stack import account_of, running_stack


async def tracked(stack) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    parts = stack.runtime.parts
    return parts.attempts.size(), parts.ledger.size()


async def test_attempt_set_is_cleared_after_success() -> None:
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "usage_limit")

        await stack.call()

        assert await tracked(stack) == (0, 0)


async def test_attempt_set_is_cleared_after_the_final_failure() -> None:
    async with running_stack(["a", "b"]) as stack:
        for sub_id in ("a", "b"):
            stack.mock.script(account_of(sub_id), "usage_limit")

        with pytest.raises(litellm.RateLimitError):
            await stack.call()

        assert await tracked(stack) == (0, 0)


async def test_attempt_set_is_cleared_when_the_client_abandons_a_stream() -> None:
    async with running_stack(["a"]) as stack:
        stream = await stack.open_stream()
        await anext(stream)
        assert await tracked(stack) == (1, 1)

        await stream.aclose()
        await stack.finished()

        assert await tracked(stack) == (0, 0)


async def test_attempt_set_is_cleared_after_a_completed_stream() -> None:
    async with running_stack(["a"]) as stack:
        stream = await stack.open_stream()

        _ = [chunk async for chunk in stream]
        await stack.finished()

        assert await tracked(stack) == (0, 0)


async def test_client_supplied_request_id_is_replaced() -> None:
    async with running_stack(["a"]) as stack:
        data: dict[str, object] = {"metadata": {"agentek_request_id": "forged"}}

        await stack.callback.async_pre_call_hook(None, None, data, "acompletion")  # type: ignore[arg-type]

        assert data["metadata"]["agentek_request_id"] != "forged"  # type: ignore[index]


async def test_client_litellm_metadata_is_dropped_on_the_chat_path() -> None:
    async with running_stack(["a", "b"]) as stack:
        forged = {"model_info": {"id": "sub:b:gpt-5.4"}, "agentek_request_id": "forged"}
        data: dict[str, object] = {"metadata": {}, "litellm_metadata": forged}

        await stack.callback.async_pre_call_hook(None, None, data, "acompletion")  # type: ignore[arg-type]

        assert "litellm_metadata" not in data
        assert data["metadata"]["agentek_request_id"] != "forged"  # type: ignore[index]


async def test_responses_requests_keep_the_metadata_the_proxy_gave_them() -> None:
    async with running_stack(["a"]) as stack:
        data: dict[str, object] = {"litellm_metadata": {"tags": ["x"]}}

        await stack.callback.async_pre_call_hook(None, None, data, "aresponses")  # type: ignore[arg-type]

        assert data["litellm_metadata"]["tags"] == ["x"]  # type: ignore[index]
        assert "agentek_request_id" in data["litellm_metadata"]  # type: ignore[operator]


async def test_forged_deployment_on_the_chat_path_does_not_move_the_attempt() -> None:
    async with running_stack(["a", "b"]) as stack:
        stack.mock.script(account_of("a"), "usage_limit")

        response = await stack.call(
            litellm_metadata={"model_info": {"id": "sub:b:gpt-5.4"}}
        )

        assert (
            response.choices[0].message.content,  # type: ignore[attr-defined]
            stack.mock.accounts_served(),
        ) == ("Hello from mock", [account_of("a"), account_of("b")])
