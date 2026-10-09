from agentek_gateway.subscriptions.model import SubscriptionState as S

from .stack import MODEL, Stack, account_of, running_stack


def attempt_kwargs(
    sub_id: str = "a", request_id: str = "r1", **extra: object
) -> dict[str, object]:
    return {
        "model": f"chatgpt/{MODEL}",
        "litellm_call_id": "call-1",
        "litellm_credential_name": f"cred-{sub_id}",
        "metadata": {
            "agentek_request_id": request_id,
            "model_group": MODEL,
            "model_info": {"id": f"sub:{sub_id}:{MODEL}"},
            "tags": ["team-x", "Credential: cred-z"],
        },
        **extra,
    }


async def reserved(stack: Stack) -> int:
    return (await stack.slot_store.in_flight(["a"]))["a"]


async def test_hook_fired_twice_for_one_attempt_reserves_one_slot() -> None:
    async with running_stack(["a"]) as stack:
        kwargs = attempt_kwargs()

        await stack.callback.async_pre_call_deployment_hook(kwargs, None)
        await stack.callback.async_pre_call_deployment_hook(kwargs, None)

        assert await reserved(stack) == 1


async def test_hook_replaces_foreign_credential_tags_and_keeps_the_rest() -> None:
    async with running_stack(["a"]) as stack:
        kwargs = attempt_kwargs()

        await stack.callback.async_pre_call_deployment_hook(kwargs, None)

        assert kwargs["metadata"]["tags"] == ["team-x", "Credential: cred-a"]  # type: ignore[index]


async def test_deployment_without_a_subscription_gets_no_slot_and_no_changes() -> None:
    async with running_stack(["a"]) as stack:
        kwargs = attempt_kwargs()
        kwargs["metadata"]["model_info"] = {"id": "deepseek-1"}  # type: ignore[index]
        kwargs["litellm_credential_name"] = "not-a-subscription"

        result = await stack.callback.async_pre_call_deployment_hook(kwargs, None)

        assert (result, await reserved(stack), kwargs["metadata"]["tags"]) == (  # type: ignore[index]
            None,
            0,
            ["team-x", "Credential: cred-z"],
        )


async def test_prompt_cache_key_becomes_a_stable_provider_session_id() -> None:
    async with running_stack(["a"]) as stack:
        first = await stack.callback.async_pre_call_deployment_hook(
            attempt_kwargs(request_id="r1", prompt_cache_key="chat-1"), None
        )
        second = await stack.callback.async_pre_call_deployment_hook(
            attempt_kwargs(request_id="r2", prompt_cache_key="chat-1"), None
        )
        other = await stack.callback.async_pre_call_deployment_hook(
            attempt_kwargs(request_id="r3", prompt_cache_key="chat-2"), None
        )
        plain = await stack.callback.async_pre_call_deployment_hook(
            attempt_kwargs(request_id="r4"), None
        )

        assert (
            first["chatgpt_session_id"] == second["chatgpt_session_id"],  # type: ignore[index]
            first["chatgpt_session_id"] != other["chatgpt_session_id"],  # type: ignore[index]
            plain["chatgpt_session_id"] if plain else None,  # type: ignore[index]
        ) == (True, True, None)


async def test_chat_keeps_its_subscription_while_a_preferred_one_comes_back() -> None:
    async with running_stack(["a", "b"], priorities={"b": 10}) as stack:
        blocked_until = stack.clock.now() + 3600
        await set_state(stack, "b", S.RATE_LIMITED, blocked_until)
        await stack.refresh()
        await stack.respond(prompt_cache_key="chat-1")
        await set_state(stack, "b", S.ACTIVE, None, version=2)
        await stack.refresh()

        await stack.respond(prompt_cache_key="chat-1")
        await stack.respond()

        assert stack.mock.accounts_served() == [
            account_of("a"),
            account_of("a"),
            account_of("b"),
        ]


async def test_provider_sees_one_session_for_a_chat_and_previous_response_id_survives_a_switch() -> (
    None
):
    async with running_stack(["a", "b"]) as stack:
        await stack.respond(prompt_cache_key="chat-1", previous_response_id="resp_1")
        stack.mock.script(account_of("a"), "usage_limit")

        await stack.respond(prompt_cache_key="chat-1", previous_response_id="resp_1")

        sessions = {item.session_id for item in stack.mock.received}
        previous = [
            item.body.get("previous_response_id") for item in stack.mock.received
        ]
        assert (len(sessions), previous, stack.mock.accounts_served()) == (
            1,
            ["resp_1", "resp_1", "resp_1"],
            [account_of("a"), account_of("a"), account_of("b")],
        )


async def set_state(
    stack: Stack, sub_id: str, state: S, until: float | None, version: int = 1
) -> None:
    from .builders import state_record

    current = await stack.store.read_state(sub_id)
    expected = current.version if current else None
    await stack.store.compare_and_set_state(
        sub_id, expected, state_record(state, until, version=(expected or 0) + 1)
    )
