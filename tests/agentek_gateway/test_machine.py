import pytest

from agentek_gateway.subscriptions.config import ProviderTuning
from agentek_gateway.subscriptions.events import (
    AccountDeactivated,
    Event,
    Expired,
    LimitExhausted,
    LimitsObserved,
    LimitWindow,
    OperatorDisabled,
    OperatorEnabled,
    Overloaded,
    ProbeFailed,
    ProbeSucceeded,
    Reauthorized,
    RefreshSucceeded,
    Restored,
    TokenRevoked,
    Unauthorized,
)
from agentek_gateway.subscriptions.machine import (
    ImplausibleReset,
    exhaustion_until,
    soft_limit_until,
    transition,
)
from agentek_gateway.subscriptions.model import (
    Limits,
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState as S,
    Window,
    effective_state,
)

NOW = 1_000_000.0
TUNING = ProviderTuning()
HOUR = 3600.0
DAY = 86400.0


def record(
    state: S, *, until: float | None = None, streak: int = 0, version: int = 3
) -> StateRecord:
    return StateRecord(
        state, version, NOW - 10, until, StateReason.NONE, SignalSource.NONE, streak
    )


def limits(
    five: tuple[float, float] | None = None, weekly: tuple[float, float] | None = None
) -> Limits:
    return Limits(Window(*five) if five else None, Window(*weekly) if weekly else None)


# (initial state, initial until, initial streak, event, expected state, expected until, expected streak)
TABLE = [
    # limit exhaustion
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + 20 * 60),
        S.RATE_LIMITED,
        NOW + 20 * 60,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.FIVE_HOUR, NOW + 4 * HOUR),
        S.RATE_LIMITED,
        NOW + 1800,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.FIVE_HOUR, NOW + 10 * 60),
        S.RATE_LIMITED,
        NOW + 600,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + 3 * DAY),
        S.RATE_LIMITED,
        NOW + 3 * DAY,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + 8 * DAY),
        S.RATE_LIMITED,
        NOW + 8 * DAY,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + 8 * DAY + 1),
        S.RATE_LIMITED,
        NOW + 300,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW - 1),
        S.RATE_LIMITED,
        NOW + 300,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW),
        S.RATE_LIMITED,
        NOW + 300,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitExhausted(LimitWindow.UNKNOWN, None),
        S.RATE_LIMITED,
        NOW + 300,
        0,
    ),
    (
        S.SOFT_LIMITED,
        NOW + HOUR,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR),
        S.RATE_LIMITED,
        NOW + HOUR,
        0,
    ),
    (
        S.HALF_OPEN,
        None,
        2,
        LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR),
        S.RATE_LIMITED,
        NOW + HOUR,
        2,
    ),
    (
        S.RATE_LIMITED,
        NOW + 100,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR),
        S.RATE_LIMITED,
        NOW + HOUR,
        0,
    ),
    (
        S.RATE_LIMITED,
        NOW + HOUR,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + 100),
        S.RATE_LIMITED,
        NOW + HOUR,
        0,
    ),
    (
        S.AUTH_REFRESHING,
        NOW + 100,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR),
        S.RATE_LIMITED,
        NOW + HOUR,
        0,
    ),
    (
        S.BROKEN,
        None,
        5,
        LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR),
        S.BROKEN,
        None,
        5,
    ),
    (
        S.BANNED,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR),
        S.BANNED,
        None,
        0,
    ),
    (
        S.DISABLED,
        None,
        0,
        LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR),
        S.DISABLED,
        None,
        0,
    ),
    # soft threshold from success headers
    (
        S.ACTIVE,
        None,
        0,
        LimitsObserved(limits(weekly=(95, NOW + 2 * HOUR))),
        S.SOFT_LIMITED,
        NOW + 2 * HOUR,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitsObserved(limits(weekly=(94.9, NOW + 2 * HOUR))),
        S.ACTIVE,
        None,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitsObserved(limits(five=(96, NOW + HOUR), weekly=(10, NOW + DAY))),
        S.SOFT_LIMITED,
        NOW + HOUR,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitsObserved(limits(five=(96, NOW + HOUR), weekly=(97, NOW + DAY))),
        S.SOFT_LIMITED,
        NOW + DAY,
        0,
    ),
    (
        S.ACTIVE,
        None,
        0,
        LimitsObserved(limits(weekly=(99, NOW - 5))),
        S.SOFT_LIMITED,
        NOW + 300,
        0,
    ),
    (
        S.SOFT_LIMITED,
        NOW + HOUR,
        0,
        LimitsObserved(limits(weekly=(10, NOW + DAY))),
        S.ACTIVE,
        None,
        0,
    ),
    (
        S.SOFT_LIMITED,
        NOW + HOUR,
        0,
        LimitsObserved(limits(weekly=(96, NOW + HOUR + 30))),
        S.SOFT_LIMITED,
        NOW + HOUR,
        0,
    ),
    (
        S.SOFT_LIMITED,
        NOW + HOUR,
        0,
        LimitsObserved(limits(weekly=(96, NOW + 2 * HOUR))),
        S.SOFT_LIMITED,
        NOW + 2 * HOUR,
        0,
    ),
    (S.ACTIVE, None, 3, LimitsObserved(limits()), S.ACTIVE, None, 0),
    (
        S.OVERLOADED,
        NOW + 100,
        2,
        LimitsObserved(limits(weekly=(99, NOW + HOUR))),
        S.OVERLOADED,
        NOW + 100,
        2,
    ),
    # overload series: pause doubles up to the cap, fifth entry breaks the subscription
    (S.ACTIVE, None, 0, Overloaded(), S.OVERLOADED, NOW + 60, 1),
    (S.ACTIVE, None, 1, Overloaded(), S.OVERLOADED, NOW + 120, 2),
    (S.ACTIVE, None, 2, Overloaded(), S.OVERLOADED, NOW + 240, 3),
    (S.ACTIVE, None, 3, Overloaded(), S.OVERLOADED, NOW + 480, 4),
    (S.ACTIVE, None, 4, Overloaded(), S.BROKEN, None, 5),
    (S.SOFT_LIMITED, NOW + HOUR, 0, Overloaded(), S.OVERLOADED, NOW + 60, 1),
    (S.OVERLOADED, NOW + 30, 1, Overloaded(), S.OVERLOADED, NOW + 30, 1),
    (S.RATE_LIMITED, NOW + HOUR, 0, Overloaded(), S.RATE_LIMITED, NOW + HOUR, 0),
    (S.AUTH_REFRESHING, NOW + 100, 0, Overloaded(), S.AUTH_REFRESHING, NOW + 100, 0),
    # probes
    (S.HALF_OPEN, None, 0, ProbeFailed(), S.OVERLOADED, NOW + 60, 1),
    (S.HALF_OPEN, None, 3, ProbeFailed(), S.OVERLOADED, NOW + 480, 4),
    (S.HALF_OPEN, None, 4, ProbeFailed(), S.BROKEN, None, 5),
    (S.BROKEN, None, 5, ProbeFailed(), S.BROKEN, None, 5),
    (S.ACTIVE, None, 0, ProbeFailed(), S.ACTIVE, None, 0),
    (S.HALF_OPEN, None, 3, ProbeSucceeded(), S.ACTIVE, None, 0),
    (S.BROKEN, None, 5, ProbeSucceeded(), S.ACTIVE, None, 0),
    (
        S.HALF_OPEN,
        None,
        0,
        ProbeSucceeded(limits(weekly=(97, NOW + HOUR))),
        S.SOFT_LIMITED,
        NOW + HOUR,
        0,
    ),
    (S.AUTH_FAILED, None, 0, ProbeSucceeded(), S.AUTH_FAILED, None, 0),
    (S.BANNED, None, 0, ProbeSucceeded(), S.BANNED, None, 0),
    (S.DISABLED, None, 0, ProbeSucceeded(), S.DISABLED, None, 0),
    # authorization
    (S.ACTIVE, None, 0, Unauthorized(), S.AUTH_REFRESHING, NOW + 600, 0),
    (S.HALF_OPEN, None, 0, Unauthorized(), S.AUTH_REFRESHING, NOW + 600, 0),
    (S.AUTH_REFRESHING, NOW + 100, 0, Unauthorized(), S.AUTH_REFRESHING, NOW + 100, 0),
    (S.AUTH_REFRESHING, NOW + 100, 0, RefreshSucceeded(), S.HALF_OPEN, None, 0),
    (S.ACTIVE, None, 0, RefreshSucceeded(), S.ACTIVE, None, 0),
    (S.AUTH_REFRESHING, NOW + 100, 0, TokenRevoked(), S.AUTH_FAILED, None, 0),
    (S.ACTIVE, None, 0, TokenRevoked(), S.AUTH_FAILED, None, 0),
    (S.BANNED, None, 0, TokenRevoked(), S.BANNED, None, 0),
    (S.AUTH_FAILED, None, 0, Reauthorized(), S.HALF_OPEN, None, 0),
    (S.AUTH_REFRESHING, NOW + 100, 0, Reauthorized(), S.HALF_OPEN, None, 0),
    (S.BANNED, None, 0, Reauthorized(), S.HALF_OPEN, None, 0),
    (S.ACTIVE, None, 0, Reauthorized(), S.ACTIVE, None, 0),
    (S.BROKEN, None, 5, Reauthorized(), S.BROKEN, None, 5),
    (S.BROKEN, None, 5, Unauthorized(), S.BROKEN, None, 5),
    # ban and restore
    (S.ACTIVE, None, 0, AccountDeactivated(), S.BANNED, None, 0),
    (S.AUTH_FAILED, None, 0, AccountDeactivated(), S.BANNED, None, 0),
    (S.BANNED, None, 0, Restored(), S.HALF_OPEN, None, 0),
    (S.ACTIVE, None, 0, Restored(), S.ACTIVE, None, 0),
    # operator
    (S.ACTIVE, None, 0, OperatorDisabled(), S.DISABLED, None, 0),
    (S.BANNED, None, 0, OperatorDisabled(), S.DISABLED, None, 0),
    (S.DISABLED, None, 0, OperatorEnabled(), S.HALF_OPEN, None, 0),
    (S.ACTIVE, None, 0, OperatorEnabled(), S.ACTIVE, None, 0),
    (S.DISABLED, None, 0, Overloaded(), S.DISABLED, None, 0),
    # expiry
    (S.RATE_LIMITED, NOW, 0, Expired(), S.HALF_OPEN, None, 0),
    (S.RATE_LIMITED, NOW + 1, 0, Expired(), S.RATE_LIMITED, NOW + 1, 0),
    (S.OVERLOADED, NOW - 1, 2, Expired(), S.HALF_OPEN, None, 2),
    (S.AUTH_REFRESHING, NOW - 1, 0, Expired(), S.HALF_OPEN, None, 0),
    (S.SOFT_LIMITED, NOW - 1, 0, Expired(), S.ACTIVE, None, 0),
    (S.BROKEN, None, 5, Expired(), S.BROKEN, None, 5),
    (S.AUTH_FAILED, None, 0, Expired(), S.AUTH_FAILED, None, 0),
]


@pytest.mark.parametrize(
    (
        "state",
        "until",
        "streak",
        "event",
        "expected_state",
        "expected_until",
        "expected_streak",
    ),
    TABLE,
)
def test_state_times_event(
    state: S,
    until: float | None,
    streak: int,
    event: Event,
    expected_state: S,
    expected_until: float | None,
    expected_streak: int,
) -> None:
    result = transition(record(state, until=until, streak=streak), event, NOW, TUNING)

    assert (
        result.record.state,
        result.record.until,
        result.record.overload_streak,
    ) == (
        expected_state,
        expected_until,
        expected_streak,
    )


@pytest.mark.parametrize(
    ("state", "until", "streak", "event"), [row[:4] for row in TABLE]
)
def test_version_grows_exactly_when_the_record_changes(
    state: S, until: float | None, streak: int, event: Event
) -> None:
    before = record(state, until=until, streak=streak)

    result = transition(before, event, NOW, TUNING)

    assert result.changed == (result.record is not before)
    assert result.record.version == before.version + (1 if result.changed else 0)


def test_overload_pause_is_capped() -> None:
    tuning = ProviderTuning(overload_base_s=400, overload_cap_s=500, broken_after=9)

    result = transition(record(S.ACTIVE, streak=1), Overloaded(), NOW, tuning)

    assert result.record.until == NOW + 500


def test_implausible_reset_is_reported() -> None:
    until, notes = exhaustion_until(LimitWindow.WEEKLY, NOW + 9 * DAY, NOW, TUNING)

    assert (until, notes) == (NOW + 300, (ImplausibleReset(NOW + 9 * DAY),))


def test_reset_inside_bounds_is_not_reported() -> None:
    _, notes = exhaustion_until(LimitWindow.WEEKLY, NOW + 3 * DAY, NOW, TUNING)

    assert notes == ()


def test_transition_carries_the_reason_and_source_of_a_limit() -> None:
    result = transition(
        record(S.ACTIVE), LimitExhausted(LimitWindow.WEEKLY, NOW + HOUR), NOW, TUNING
    )

    assert (result.record.reason, result.record.source) == (
        StateReason.LIMIT_EXHAUSTED,
        SignalSource.PROVIDER_RESPONSE,
    )


def test_soft_limit_ignores_windows_below_the_threshold() -> None:
    assert (
        soft_limit_until(
            limits(five=(50, NOW + HOUR), weekly=(94, NOW + DAY)), NOW, TUNING
        )
        is None
    )


def test_configured_threshold_replaces_the_default() -> None:
    tuning = ProviderTuning(soft_threshold_percent=80)

    assert soft_limit_until(limits(weekly=(85, NOW + HOUR)), NOW, tuning) == NOW + HOUR


def test_configured_five_hour_cap_replaces_the_default() -> None:
    tuning = ProviderTuning(five_hour_block_cap_s=120)

    result = transition(
        record(S.ACTIVE), LimitExhausted(LimitWindow.FIVE_HOUR, NOW + HOUR), NOW, tuning
    )

    assert result.record.until == NOW + 120


@pytest.mark.parametrize(
    ("state", "until", "expected"),
    [
        (S.RATE_LIMITED, NOW - 1, S.HALF_OPEN),
        (S.RATE_LIMITED, NOW + 1, S.RATE_LIMITED),
        (S.OVERLOADED, NOW, S.HALF_OPEN),
        (S.AUTH_REFRESHING, NOW - 1, S.HALF_OPEN),
        (S.SOFT_LIMITED, NOW, S.ACTIVE),
        (S.BROKEN, NOW - 1, S.BROKEN),
        (S.ACTIVE, None, S.ACTIVE),
    ],
)
def test_effective_state_applies_an_elapsed_deadline(
    state: S, until: float | None, expected: S
) -> None:
    assert effective_state(record(state, until=until), NOW) is expected
