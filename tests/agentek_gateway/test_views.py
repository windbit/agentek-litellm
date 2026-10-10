from agentek_gateway.subscriptions.model import (
    Limits,
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState as S,
    UsageRecord,
    UsageSource,
    Window,
)
from agentek_gateway.subscriptions.providers.chatgpt_profile import Profile
from agentek_gateway.subscriptions.views import (
    is_working_now,
    subscription_view,
    utc_iso,
)

from .conftest import make_subscription

NOW = 1_800_000_000.0


def record(state: S, until: float | None = None) -> StateRecord:
    return StateRecord(
        state,
        3,
        NOW - 60,
        until,
        StateReason.LIMIT_EXHAUSTED,
        SignalSource.PROVIDER_RESPONSE,
        0,
    )


def test_times_are_utc_instants_with_a_z_suffix() -> None:
    assert utc_iso(0) == "1970-01-01T00:00:00Z"


def test_the_view_names_the_state_the_reason_the_deadline_and_the_limits() -> None:
    usage = UsageRecord(
        Limits(weekly=Window(41.5, NOW + 3600)), NOW - 5, UsageSource.USAGE_CHECK
    )

    view = subscription_view(
        make_subscription("a", priority=7),
        record(S.RATE_LIMITED, NOW + 120),
        usage,
        Profile("o@x.test", "plus"),
        NOW,
    ).as_json()

    assert view == {
        "id": "a",
        "provider": "chatgpt",
        "name": "name-a",
        "email": "o@x.test",
        "plan": "plus",
        "enabled": True,
        "priority": 7,
        "concurrency_limit": None,
        "state": {
            "state": "RATE_LIMITED",
            "reason": "limit_exhausted",
            "source": "provider_response",
            "until": utc_iso(NOW + 120),
            "entered_at": utc_iso(NOW - 60),
        },
        "limits": {
            "five_hour": None,
            "weekly": {"used_percent": 41.5, "reset_at": utc_iso(NOW + 3600)},
            "observed_at": utc_iso(NOW - 5),
            "source": "usage_check",
        },
    }


def test_a_block_that_has_run_out_is_shown_as_waiting_for_a_probe() -> None:
    view = subscription_view(
        make_subscription("a"),
        record(S.RATE_LIMITED, NOW - 1),
        None,
        Profile(None, None),
        NOW,
    )

    assert view.state.state == "HALF_OPEN"


def test_a_switched_off_subscription_is_disabled_whatever_its_record_says() -> None:
    subscription = make_subscription("a", enabled=False)

    view = subscription_view(subscription, None, None, Profile(None, None), NOW)

    assert (view.state.state, is_working_now(subscription, None, NOW)) == (
        "DISABLED",
        False,
    )


def test_a_subscription_without_a_record_is_active_and_working() -> None:
    subscription = make_subscription("a")

    view = subscription_view(subscription, None, None, Profile(None, None), NOW)

    assert (view.state.state, view.limits, is_working_now(subscription, None, NOW)) == (
        "ACTIVE",
        None,
        True,
    )
