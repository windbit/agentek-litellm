import litellm
import pytest

from agentek_gateway.subscriptions.errors import (
    NoAvailableSubscriptionsError,
    is_internal_error,
    no_capacity_message,
    retry_after_for_upstream,
)


def test_error_is_final_for_the_router_yet_reported_as_429_with_retry_after() -> None:
    error = NoAvailableSubscriptionsError("gpt-x", None, 10)

    assert (
        isinstance(error, litellm.NotFoundError),
        error.status_code,
        error.headers,
    ) == (True, 429, {"retry-after": "10"})


def test_error_is_marked_internal() -> None:
    assert is_internal_error(NoAvailableSubscriptionsError("gpt-x", None, 10))


def test_foreign_errors_are_not_internal() -> None:
    assert not is_internal_error(litellm.RateLimitError("x", "chatgpt", "gpt-x"))


def test_retry_after_value_is_configurable() -> None:
    assert NoAvailableSubscriptionsError("m", None, 25).headers == {"retry-after": "25"}


@pytest.mark.parametrize(
    ("recovery_in_s", "text"),
    [
        (None, "No working subscriptions are available"),
        (30, "All subscriptions are exhausted, capacity returns in about 1 min"),
        (600, "All subscriptions are exhausted, capacity returns in about 10 min"),
        (7200, "All subscriptions are exhausted, capacity returns in about 2 h"),
    ],
)
def test_message_names_the_recovery_time_when_it_is_known(
    recovery_in_s: float | None, text: str
) -> None:
    assert (
        no_capacity_message(recovery_in_s),
        NoAvailableSubscriptionsError("m", recovery_in_s, 10).message,
    ) == (text, text)


def test_upstream_429_without_retry_after_gets_the_header() -> None:
    assert retry_after_for_upstream(429, {"content-type": "json"}, 10) == {
        "retry-after": "10"
    }


def test_upstream_429_that_already_has_retry_after_is_left_alone() -> None:
    assert retry_after_for_upstream(429, {"Retry-After": "3"}, 10) == {}


@pytest.mark.parametrize("status", [200, 400, 500, 529])
def test_other_statuses_get_no_header(status: int) -> None:
    assert retry_after_for_upstream(status, {}, 10) == {}
