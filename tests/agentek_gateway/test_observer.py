import pytest
from litellm.llms.base_llm.chat.transformation import BaseLLMException
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.subscriptions.providers.base import (
    ErrorClass,
    ModelNotSupported,
    Unclassified,
)
from agentek_gateway.subscriptions.providers.chatgpt import classify_error
from agentek_gateway.subscriptions.providers.observer import (
    AttemptContext,
    ObservedFailure,
    current_attempt,
    install_error_observer,
    status_for_client,
    uninstall_error_observer,
)

from .test_chatgpt_provider import NOW, fixture


def classify(status: int, headers, body: str) -> ErrorClass:  # type: ignore[no-untyped-def]
    return classify_error(status, headers, body, now=NOW)


def context(alternatives: int = 1, attempt: int = 1) -> AttemptContext:
    return AttemptContext("req-1", attempt, "sub-a", "sub:sub-a:gpt-x", alternatives)


@pytest.fixture
def observed():  # type: ignore[no-untyped-def]
    seen: list[ObservedFailure] = []
    install_error_observer(ChatGPTResponsesAPIConfig, classify, seen.append)
    yield seen
    uninstall_error_observer(ChatGPTResponsesAPIConfig)


def error_status(config: ChatGPTResponsesAPIConfig, name: str) -> int:
    data = fixture(name)
    with pytest.raises(BaseLLMException) as raised:
        config.get_error_class(data["body"], data["status"], data["headers"])
    return raised.value.status_code


def test_model_not_supported_is_rewritten_while_another_candidate_is_left(observed) -> None:  # type: ignore[no-untyped-def]
    token = current_attempt.set(context(alternatives=2))
    try:
        status = error_status(
            ChatGPTResponsesAPIConfig(), "error_400_model_not_supported.json"
        )
    finally:
        current_attempt.reset(token)

    assert status == 409


def test_last_candidate_keeps_the_original_400(observed) -> None:  # type: ignore[no-untyped-def]
    token = current_attempt.set(context(alternatives=0))
    try:
        status = error_status(
            ChatGPTResponsesAPIConfig(), "error_400_model_not_supported.json"
        )
    finally:
        current_attempt.reset(token)

    assert status == 400


def test_other_errors_keep_their_status(observed) -> None:  # type: ignore[no-untyped-def]
    token = current_attempt.set(context())
    try:
        statuses = [
            error_status(ChatGPTResponsesAPIConfig(), name)
            for name in (
                "error_400_input_must_be_list.json",
                "error_429_usage_limit.json",
                "error_401_token_revoked.json",
            )
        ]
    finally:
        current_attempt.reset(token)

    assert statuses == [400, 429, 401]


def test_every_attempt_error_is_reported_once_with_its_context(observed) -> None:  # type: ignore[no-untyped-def]
    token = current_attempt.set(context(attempt=3))
    try:
        error_status(ChatGPTResponsesAPIConfig(), "error_503_circuit_open.json")
    finally:
        current_attempt.reset(token)

    assert [(item.context.attempt, item.status, item.error) for item in observed] == [
        (3, 503, Unclassified(immediate=False, recognized=True))
    ]


def test_original_status_and_body_are_reported_not_the_rewritten_one(observed) -> None:  # type: ignore[no-untyped-def]
    token = current_attempt.set(context(alternatives=1))
    try:
        error_status(ChatGPTResponsesAPIConfig(), "error_400_model_not_supported.json")
    finally:
        current_attempt.reset(token)

    assert (observed[0].status, observed[0].error) == (
        400,
        ModelNotSupported("gpt-6.1-sol"),
    )


def test_requests_outside_a_subscription_attempt_are_not_observed(observed) -> None:  # type: ignore[no-untyped-def]
    status = error_status(
        ChatGPTResponsesAPIConfig(), "error_400_model_not_supported.json"
    )

    assert (status, observed) == (400, [])


def test_installing_twice_wraps_once() -> None:
    seen: list[ObservedFailure] = []
    first = install_error_observer(ChatGPTResponsesAPIConfig, classify, seen.append)
    second = install_error_observer(ChatGPTResponsesAPIConfig, classify, seen.append)
    token = current_attempt.set(context())
    try:
        error_status(ChatGPTResponsesAPIConfig(), "error_503_circuit_open.json")
    finally:
        current_attempt.reset(token)
        uninstall_error_observer(ChatGPTResponsesAPIConfig)

    assert (first, second, len(seen)) == (True, False, 1)


def test_uninstall_restores_the_original_method() -> None:
    original = ChatGPTResponsesAPIConfig.get_error_class
    install_error_observer(ChatGPTResponsesAPIConfig, classify, lambda failure: None)

    uninstall_error_observer(ChatGPTResponsesAPIConfig)

    assert ChatGPTResponsesAPIConfig.get_error_class is original


def test_get_error_class_signature_is_the_one_the_observer_wraps() -> None:
    import inspect

    parameters = list(
        inspect.signature(ChatGPTResponsesAPIConfig.get_error_class).parameters
    )

    assert parameters == ["self", "error_message", "status_code", "headers"]


@pytest.mark.parametrize(("alternatives", "expected"), [(0, 400), (1, 409), (5, 409)])
def test_status_decision_depends_on_the_remaining_candidates(
    alternatives: int, expected: int
) -> None:
    assert (
        status_for_client(ModelNotSupported("m"), 400, context(alternatives))
        == expected
    )
