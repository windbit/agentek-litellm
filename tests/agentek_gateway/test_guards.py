"""Tripwires on the LiteLLM surface the plugin depends on: an upstream merge that moves any of it fails here."""

import inspect

import litellm
import pytest
from litellm import Router
from litellm.integrations.custom_logger import CustomLogger
from litellm.llms.chatgpt.common_utils import get_chatgpt_session_id
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.subscriptions.adapter import SubscriptionCallback
from agentek_gateway.subscriptions.plugin import default_plugins
from agentek_gateway.subscriptions.stickiness import SESSION_ID_PARAM

from .plain import MODEL as PLAIN_MODEL
from .conftest import make_subscription
from .plain import plain_runtime
from .stack import MODEL, deployment_for

HOOKS = (
    "async_pre_call_hook",
    "async_filter_deployments",
    "async_pre_call_deployment_hook",
    "async_log_success_event",
    "async_log_failure_event",
    "async_post_call_failure_hook",
    "async_post_call_response_headers_hook",
    "async_post_call_streaming_iterator_hook",
)


def parameter_names(function: object) -> list[str]:
    return list(inspect.signature(function).parameters)  # type: ignore[arg-type]


@pytest.mark.parametrize("hook", HOOKS)
def test_hook_signature_matches_the_one_litellm_calls(hook: str) -> None:
    ours, theirs = getattr(SubscriptionCallback, hook), getattr(CustomLogger, hook)

    assert parameter_names(ours) == parameter_names(theirs)


@pytest.mark.parametrize("hook", HOOKS)
def test_hook_is_defined_on_the_callback_class_itself(hook: str) -> None:
    assert hook in SubscriptionCallback.__dict__


def test_stream_hook_is_an_async_generator_like_litellms() -> None:
    assert inspect.isasyncgenfunction(
        SubscriptionCallback.async_post_call_streaming_iterator_hook
    ) and inspect.isasyncgenfunction(
        CustomLogger.async_post_call_streaming_iterator_hook
    )


def test_header_hook_still_accepts_the_call_info_litellm_passes() -> None:
    assert "litellm_call_info" in parameter_names(
        SubscriptionCallback.async_post_call_response_headers_hook
    )


def test_provider_error_class_hook_keeps_the_arguments_the_observer_wraps() -> None:
    assert parameter_names(ChatGPTResponsesAPIConfig.get_error_class) == [
        "self",
        "error_message",
        "status_code",
        "headers",
    ]


def test_default_plugin_registers_the_callback_class() -> None:
    (plugin,) = default_plugins()

    assert plugin.callback_factories == (SubscriptionCallback,)


def test_router_still_names_the_exclusion_list_the_filter_reads() -> None:
    assert "_excluded_deployment_ids" in inspect.getsource(litellm.router)


def test_provider_prefers_the_plugin_session_id_over_litellm_session_id() -> None:
    params = {SESSION_ID_PARAM: "plugin", "litellm_session_id": "trace-bound"}

    assert get_chatgpt_session_id(params) == "plugin"


def test_litellm_session_id_still_drives_the_trace_id_the_plugin_must_not_touch() -> (
    None
):
    from litellm.litellm_core_utils import litellm_logging

    assert "litellm_session_id" in inspect.getsource(litellm_logging)


def test_router_skips_the_retry_pause_when_other_healthy_deployments_exist() -> None:
    router = Router(
        model_list=[deployment_for("a", "http://x"), deployment_for("b", "http://x")]
    )
    pair = router.model_list

    pause = router._time_to_sleep_before_retry(  # noqa: SLF001
        Exception("x"), 3, 4, healthy_deployments=pair, all_deployments=pair
    )

    assert pause == 0


def test_router_pauses_before_retrying_a_single_deployment_group() -> None:
    router = Router(model_list=[deployment_for("a", "http://x")])
    single = router.model_list

    pause = router._time_to_sleep_before_retry(  # noqa: SLF001
        Exception("x"), 3, 4, healthy_deployments=single, all_deployments=single
    )

    assert pause > 0


def test_batch_cooldown_read_threshold_is_the_constant_the_chart_overrides() -> None:
    from litellm import constants

    assert "DEFAULT_MAX_REDIS_BATCH_CACHE_SIZE" in inspect.getsource(constants)
    assert constants.DEFAULT_MAX_REDIS_BATCH_CACHE_SIZE > 0


class RequestShapeProbe(CustomLogger):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[dict[str, object]] = []

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):  # type: ignore[no-untyped-def]
        self.seen.append(dict(request_kwargs or {}))
        return healthy_deployments


async def test_chat_and_responses_requests_keep_their_metadata_container_names() -> (
    None
):
    from .stack import running_stack

    probe = RequestShapeProbe()
    async with running_stack(["a"]) as stack:
        litellm.callbacks.append(probe)
        await stack.call(prompt_cache_key="chat-1")
        await stack.respond(prompt_cache_key="chat-1")

        chat, responses = probe.seen[-2], probe.seen[-1]
        assert (
            isinstance(chat.get("metadata"), dict),
            isinstance(responses.get("litellm_metadata"), dict),
            chat.get("prompt_cache_key"),
            responses.get("prompt_cache_key"),
        ) == (True, True, "chat-1", "chat-1")


async def test_failure_inside_the_hook_leaves_non_subscription_deployments_available() -> (
    None
):
    from dataclasses import replace

    from agentek_gateway.subscriptions.attempts import AttemptTracker
    from agentek_gateway.subscriptions.runtime import (
        RuntimeSlot,
        SubscriptionRuntime,
    )
    from agentek_gateway.subscriptions.gateway import SubscriptionGateway
    from agentek_gateway.subscriptions.outcomes import OutcomeTracker

    class Exploding(AttemptTracker):
        def attempted(self, request_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    plain = plain_runtime(["a"], shared=True)
    await plain.runtime.parts.snapshot.refresh()
    parts = replace(plain.runtime.parts, attempts=Exploding(plain.clock, 60.0))
    runtime = SubscriptionRuntime(
        parts,
        SubscriptionGateway(parts),
        OutcomeTracker(parts),
        plain.runtime.toggle,
        plain.runtime.states,
    )
    callback = SubscriptionCallback(RuntimeSlot(runtime))

    offered = await callback.async_filter_deployments(
        PLAIN_MODEL, plain.deployments, None, {"metadata": {"agentek_request_id": "r"}}
    )

    assert [item["model_info"]["id"] for item in offered] == ["deepseek-1"]  # type: ignore[index]


async def test_failure_inside_the_hook_on_a_subscription_only_group_gives_the_429() -> (
    None
):
    from dataclasses import replace

    from agentek_gateway.subscriptions.attempts import AttemptTracker
    from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
    from agentek_gateway.subscriptions.gateway import SubscriptionGateway
    from agentek_gateway.subscriptions.outcomes import OutcomeTracker
    from agentek_gateway.subscriptions.runtime import RuntimeSlot, SubscriptionRuntime

    class Exploding(AttemptTracker):
        def attempted(self, request_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    plain = plain_runtime(["a"])
    await plain.runtime.parts.snapshot.refresh()
    parts = replace(plain.runtime.parts, attempts=Exploding(plain.clock, 60.0))
    runtime = SubscriptionRuntime(
        parts,
        SubscriptionGateway(parts),
        OutcomeTracker(parts),
        plain.runtime.toggle,
        plain.runtime.states,
    )
    callback = SubscriptionCallback(RuntimeSlot(runtime))

    with pytest.raises(NoAvailableSubscriptionsError):
        await callback.async_filter_deployments(
            PLAIN_MODEL,
            plain.deployments,
            None,
            {"metadata": {"agentek_request_id": "r"}},
        )


async def test_deployments_without_subscriptions_pass_the_filter_untouched() -> None:
    plain = plain_runtime([], shared=True)
    await plain.runtime.parts.snapshot.refresh()

    assert await plain.pick() == ["deepseek-1"]


async def test_hooks_for_a_request_without_any_subscription_do_nothing_and_do_not_raise() -> (
    None
):
    plain = plain_runtime([], shared=True)
    await plain.runtime.parts.snapshot.refresh()
    callback = SubscriptionCallback(
        __import__(
            "agentek_gateway.subscriptions.runtime", fromlist=["RuntimeSlot"]
        ).RuntimeSlot(plain.runtime)
    )
    kwargs = {"model": "deepseek/x", "metadata": {"model_info": {"id": "deepseek-1"}}}

    changed = await callback.async_pre_call_deployment_hook(kwargs, None)
    await callback.async_log_success_event(kwargs, None, None, None)  # type: ignore[arg-type]
    await callback.async_log_failure_event({**kwargs, "exception": RuntimeError("x")}, None, None, None)  # type: ignore[arg-type]
    headers = await callback.async_post_call_response_headers_hook(kwargs, None, None)

    assert (changed, headers) == (None, None)
    _ = MODEL


async def test_model_without_a_plugin_id_is_not_dropped_by_the_closed_filter() -> None:
    plain = plain_runtime([], shared=True)
    chatgpt_like = {
        "model_name": PLAIN_MODEL,
        "litellm_params": {"model": "chatgpt/gpt-x"},
        "model_info": {"id": "console-made-1"},
    }

    kept = plain.runtime.gateway.without_subscriptions(PLAIN_MODEL, [chatgpt_like])

    assert kept == [chatgpt_like]


async def test_exhausted_subscriptions_are_reported_without_a_logged_error(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
    from agentek_gateway.subscriptions.runtime import RuntimeSlot

    plain = plain_runtime(["a"])
    await plain.runtime.parts.snapshot.refresh()
    await plain.store.compare_and_set_state("a", None, _banned())
    await plain.runtime.parts.snapshot.refresh()
    callback = SubscriptionCallback(RuntimeSlot(plain.runtime))

    with caplog.at_level(logging.ERROR), pytest.raises(NoAvailableSubscriptionsError):
        await callback.async_filter_deployments(
            PLAIN_MODEL,
            plain.deployments,
            None,
            {"metadata": {"agentek_request_id": "r"}},
        )

    assert [
        record for record in caplog.records if record.levelno >= logging.ERROR
    ] == []


def _banned():  # type: ignore[no-untyped-def]
    from agentek_gateway.subscriptions.model import SubscriptionState

    from .builders import state_record

    return state_record(SubscriptionState.BANNED)


async def test_deployment_of_a_known_subscription_credential_counts_as_a_subscription() -> (
    None
):
    plain = plain_runtime([], shared=True)
    plain.repo.put(make_subscription("a"))
    await plain.runtime.parts.snapshot.refresh()
    credential_bound = {
        "model_name": PLAIN_MODEL,
        "litellm_params": {
            "model": "chatgpt/gpt-x",
            "litellm_credential_name": "cred-a",
        },
        "model_info": {"id": "legacy-1"},
    }

    kept = plain.runtime.gateway.without_subscriptions(
        PLAIN_MODEL, [credential_bound, *plain.deployments]
    )

    assert kept == plain.deployments


async def test_until_the_runtime_is_ready_subscription_deployments_are_not_offered() -> (
    None
):
    from agentek_gateway.subscriptions.runtime import RuntimeSlot

    plain = plain_runtime(["a"], shared=True)
    callback = SubscriptionCallback(RuntimeSlot())

    offered = await callback.async_filter_deployments(
        PLAIN_MODEL, plain.deployments, None, {}
    )

    assert [item["model_info"]["id"] for item in offered] == ["deepseek-1"]  # type: ignore[index]


async def test_until_the_runtime_is_ready_a_subscription_only_group_gets_the_pool_429() -> (
    None
):
    from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
    from agentek_gateway.subscriptions.runtime import RuntimeSlot

    plain = plain_runtime(["a"])
    callback = SubscriptionCallback(RuntimeSlot())

    with pytest.raises(NoAvailableSubscriptionsError) as raised:
        await callback.async_filter_deployments(
            PLAIN_MODEL, plain.deployments, None, {}
        )

    assert raised.value.headers == {"retry-after": "10"}


def hook_kwargs(
    deployment_id: str, request_id: str = "r1", **extra: object
) -> dict[str, object]:
    return {
        "model": "x",
        "litellm_call_id": "call-1",
        "metadata": {
            "agentek_request_id": request_id,
            "model_group": PLAIN_MODEL,
            "model_info": {"id": deployment_id},
        },
        **extra,
    }


async def test_filter_alone_records_no_attempt_and_no_chat_binding() -> None:
    plain = plain_runtime(["a"], shared=True)
    await plain.runtime.parts.snapshot.refresh()

    await plain.pick("r1", prompt_cache_key="chat-1")

    parts = plain.runtime.parts
    assert (parts.attempts.attempted("r1"), await parts.sticky.lookup("chat-1")) == (
        frozenset(),
        None,
    )


async def test_attempt_and_chat_binding_follow_the_deployment_the_router_took() -> None:
    from agentek_gateway.subscriptions.runtime import RuntimeSlot

    plain = plain_runtime(["a"], shared=True)
    await plain.runtime.parts.snapshot.refresh()
    callback = SubscriptionCallback(RuntimeSlot(plain.runtime))
    await plain.pick("r1", prompt_cache_key="chat-1")

    await callback.async_pre_call_deployment_hook(
        hook_kwargs("sub:a:gpt-x", prompt_cache_key="chat-1"), None
    )

    parts = plain.runtime.parts
    assert (parts.attempts.attempted("r1"), await parts.sticky.lookup("chat-1")) == (
        frozenset({"sub:a:gpt-x"}),
        "a",
    )


async def test_router_taking_a_shared_deployment_leaves_no_attempt_and_no_binding() -> (
    None
):
    from agentek_gateway.subscriptions.runtime import RuntimeSlot

    plain = plain_runtime(["a"], shared=True)
    await plain.runtime.parts.snapshot.refresh()
    callback = SubscriptionCallback(RuntimeSlot(plain.runtime))
    await plain.pick("r1", prompt_cache_key="chat-1")

    await callback.async_pre_call_deployment_hook(
        hook_kwargs("deepseek-1", prompt_cache_key="chat-1"), None
    )

    parts = plain.runtime.parts
    assert (parts.attempts.attempted("r1"), await parts.sticky.lookup("chat-1")) == (
        frozenset(),
        None,
    )
