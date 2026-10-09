import json
from pathlib import Path

import pytest

from agentek_gateway.subscriptions.events import LimitWindow
from agentek_gateway.subscriptions.model import Limits, Window
from agentek_gateway.subscriptions.providers.base import (
    AccountBanned,
    AuthRejected,
    LimitReached,
    ModelNotSupported,
    RequestRejected,
    Unclassified,
)
from agentek_gateway.subscriptions.providers.chatgpt import (
    classify_error,
    classify_stream_failure,
    mask_secrets,
    normalize_headers,
    parse_limits,
    redact_for_log,
)

FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt"
NOW = 1_791_343_500.0


def fixture(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text())


def classify_fixture(name: str):  # type: ignore[no-untyped-def]
    data = fixture(name)
    return classify_error(data["status"], data["headers"], data["body"], now=NOW)


# limits


def test_weekly_window_from_real_success_headers() -> None:
    headers = fixture("success_headers_stream.json")["headers"]

    limits = parse_limits(headers, None, now=NOW)

    assert limits == Limits(five_hour=None, weekly=Window(81.0, 1791948566.0))


def test_unused_secondary_window_without_reset_is_ignored() -> None:
    headers = fixture("success_headers_responses.json")["headers"]

    limits = parse_limits(headers, None, now=NOW)

    assert limits is not None and limits.five_hour is None


def test_both_windows_are_told_apart_by_their_length() -> None:
    headers = {
        "x-codex-primary-used-percent": "12",
        "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-at": "5000",
        "x-codex-secondary-used-percent": "40",
        "x-codex-secondary-window-minutes": "10080",
        "x-codex-secondary-reset-at": "9000",
    }

    assert parse_limits(headers, None, now=NOW) == Limits(
        Window(12, 5000), Window(40, 9000)
    )


def test_without_window_lengths_the_pair_means_five_hours_and_week() -> None:
    headers = {
        "x-codex-primary-used-percent": "12",
        "x-codex-primary-reset-at": "5000",
        "x-codex-secondary-used-percent": "40",
        "x-codex-secondary-reset-at": "9000",
    }

    assert parse_limits(headers, None, now=NOW) == Limits(
        Window(12, 5000), Window(40, 9000)
    )


def test_without_window_lengths_a_lone_primary_means_the_week() -> None:
    headers = {"x-codex-primary-used-percent": "12", "x-codex-primary-reset-at": "5000"}

    assert parse_limits(headers, None, now=NOW) == Limits(None, Window(12, 5000))


def test_reset_after_seconds_is_used_when_the_absolute_time_is_missing() -> None:
    headers = {
        "x-codex-primary-used-percent": "30",
        "x-codex-primary-reset-after-seconds": "600",
        "x-codex-primary-window-minutes": "10080",
    }

    assert parse_limits(headers, None, now=NOW) == Limits(None, Window(30, NOW + 600))


def test_provider_prefix_and_header_case_do_not_matter() -> None:
    headers = {
        "LLM_Provider-X-Codex-Primary-Used-Percent": "7",
        "llm_provider-x-codex-primary-reset-at": "5000",
        "llm_provider-x-codex-primary-window-minutes": "10080",
    }

    assert parse_limits(headers, None, now=NOW) == Limits(None, Window(7, 5000))


def test_headers_without_codex_fields_give_no_limits() -> None:
    assert parse_limits({"content-type": "text/event-stream"}, None, now=NOW) is None


def test_exhausted_window_is_read_from_the_429_body_when_headers_are_missing() -> None:
    body = json.loads(fixture("error_429_usage_limit.json")["body"])

    assert parse_limits({}, body, now=NOW) == Limits(None, Window(100.0, 1791580236.0))


def test_normalize_headers_strips_the_prefix() -> None:
    assert normalize_headers({"llm_provider-X-Codex-Plan-Type": "pro"}) == {
        "x-codex-plan-type": "pro"
    }


# classification of real responses


def test_usage_limit_carries_the_weekly_window_and_the_reset_time() -> None:
    assert classify_fixture("error_429_usage_limit.json") == LimitReached(
        LimitWindow.WEEKLY, 1791580236.0
    )


def test_usage_limit_without_headers_still_resolves_the_reset_time() -> None:
    data = fixture("error_429_usage_limit.json")

    assert classify_error(429, {}, data["body"], now=NOW) == LimitReached(
        LimitWindow.WEEKLY, 1791580236.0
    )


def test_usage_limit_with_a_five_hour_window() -> None:
    body = json.dumps(
        {
            "error": {
                "type": "usage_limit_reached",
                "limit_window_minutes": 300,
                "resets_in_seconds": 600,
            }
        }
    )

    assert classify_error(429, {}, body, now=NOW) == LimitReached(
        LimitWindow.FIVE_HOUR, NOW + 600
    )


def test_usage_limit_without_any_reset_information() -> None:
    body = json.dumps({"error": {"type": "usage_limit_reached"}})

    assert classify_error(429, {}, body, now=NOW) == LimitReached(
        LimitWindow.UNKNOWN, None
    )


def test_full_window_in_headers_is_a_limit_even_with_an_unknown_body() -> None:
    headers = {
        "x-codex-primary-used-percent": "100",
        "x-codex-primary-reset-at": "5000",
        "x-codex-primary-window-minutes": "10080",
    }

    assert classify_error(429, headers, "{}", now=NOW) == LimitReached(
        LimitWindow.WEEKLY, 5000.0
    )


def test_plain_rate_limit_without_a_usage_signal_is_unrecognized() -> None:
    body = json.dumps(
        {"error": {"type": "rate_limit_exceeded", "message": "Slow down"}}
    )

    assert classify_error(429, {}, body, now=NOW) == Unclassified(
        immediate=False, recognized=False
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("error_400_model_not_supported.json", ModelNotSupported("gpt-6.1-sol")),
        ("error_404_model_not_found.json", ModelNotSupported("gpt-5.5")),
        ("error_400_input_must_be_list.json", RequestRejected(400)),
        ("error_401_token_revoked.json", AuthRejected()),
        ("error_401_invalid_api_key.json", AuthRejected()),
        ("error_401_account_deactivated.json", AccountBanned()),
        ("error_503_circuit_open.json", Unclassified(immediate=False, recognized=True)),
        (
            "error_403_blocked_html.json",
            Unclassified(immediate=False, recognized=False),
        ),
    ],
)
def test_real_error_responses_are_classified(name: str, expected) -> None:  # type: ignore[no-untyped-def]
    assert classify_fixture(name) == expected


def test_status_529_trips_the_overload_rule_at_once() -> None:
    assert classify_error(529, {}, "overloaded", now=NOW) == Unclassified(
        immediate=True, recognized=True
    )


@pytest.mark.parametrize("status", [500, 502, 504])
def test_server_errors_are_recognized_unclassified_errors(status: int) -> None:
    assert classify_error(status, {}, "oops", now=NOW) == Unclassified(
        immediate=False, recognized=True
    )


def test_unknown_client_error_with_a_json_body_is_unrecognized() -> None:
    assert classify_error(418, {}, '{"x": 1}', now=NOW) == Unclassified(
        immediate=False, recognized=False
    )


def test_model_not_supported_needs_the_exact_status() -> None:
    body = fixture("error_400_model_not_supported.json")["body"]

    assert classify_error(500, {}, body, now=NOW) == Unclassified(
        immediate=False, recognized=True
    )


def test_deactivation_wins_over_the_status() -> None:
    body = fixture("error_401_account_deactivated.json")["body"]

    assert classify_error(403, {}, body, now=NOW) == AccountBanned()


# stream failures


def test_overload_inside_a_stream_is_a_recognized_unclassified_error() -> None:
    event = fixture("stream_failure_server_is_overloaded.json")["event"]

    assert classify_stream_failure(event, now=NOW) == Unclassified(
        immediate=False, recognized=True
    )


def test_usage_limit_event_in_a_stream_resolves_the_window() -> None:
    event = fixture("stream_error_usage_limit.json")["event"]

    assert classify_stream_failure(event, now=NOW) == LimitReached(
        LimitWindow.WEEKLY, 1791580236.0
    )


def test_unknown_stream_failure_is_unrecognized() -> None:
    event = {
        "type": "response.failed",
        "response": {"error": {"code": "brand_new", "message": "?"}},
    }

    assert classify_stream_failure(event, now=NOW) == Unclassified(
        immediate=False, recognized=False
    )


def test_flat_error_event_shape_is_understood() -> None:
    assert classify_stream_failure(
        {"type": "error", "code": "server_is_overloaded"}, now=NOW
    ) == Unclassified(immediate=False, recognized=True)


# log redaction


def test_unrecognized_response_log_drops_credentials_and_masks_secrets() -> None:
    headers = {
        "Authorization": "Bearer abcdefghijkl",
        "Set-Cookie": "a=b",
        "x-request-id": "r1",
        "Cookie": "c=d",
    }
    jwt_like = ".".join(["eyJ" + "a" * 12, "b" * 12, "sig"])
    body = f"user person@example.com token {jwt_like} key " + "sk" + "-" + "x" * 14

    logged = json.loads(redact_for_log("main", 500, headers, body))

    assert logged == {
        "subscription": "main",
        "status": 500,
        "headers": {"x-request-id": "r1"},
        "body": "user *** token *** key ***",
    }


def test_log_keeps_only_the_first_two_kilobytes_of_the_body() -> None:
    logged = json.loads(redact_for_log("main", 500, {}, "a" * 5000))

    assert len(logged["body"]) == 2048


def test_log_does_not_split_a_multibyte_character_into_garbage() -> None:
    logged = json.loads(redact_for_log("main", 500, {}, "é" * 2000))

    assert logged["body"].replace("é", "") in ("", "�")


def test_bearer_tokens_are_masked() -> None:
    assert (
        mask_secrets("Authorization: Bearer abcdefgh12345678") == "Authorization: ***"
    )
