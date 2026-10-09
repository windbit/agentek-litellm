from dataclasses import dataclass, replace

from .compat import assert_never
from .config import ProviderTuning
from .events import (
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
    SeriesCleared,
    Succeeded,
    TokenRevoked,
    Unauthorized,
)
from .model import (
    SEVERITY,
    Limits,
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState,
)

State = SubscriptionState


@dataclass(frozen=True, slots=True)
class ImplausibleReset:
    reset_at: float


Note = ImplausibleReset


@dataclass(frozen=True, slots=True)
class Entry:
    state: SubscriptionState
    reason: StateReason
    source: SignalSource
    until: float | None = None
    streak: int | None = None
    notes: tuple[Note, ...] = ()


@dataclass(frozen=True, slots=True)
class Transition:
    record: StateRecord
    changed: bool
    notes: tuple[Note, ...] = ()


def transition(
    current: StateRecord, event: Event, now: float, tuning: ProviderTuning
) -> Transition:
    if current.state is State.DISABLED and (
        not isinstance(event, (OperatorEnabled, OperatorDisabled))
    ):
        return _unchanged(current)
    match event:
        case (
            OperatorDisabled()
            | OperatorEnabled()
            | Reauthorized()
            | Restored()
            | RefreshSucceeded()
        ):
            return _manual_transition(current, event, now)
        case Expired():
            return _expire(current, now)
        case ProbeSucceeded(limits=limits):
            return _probe_succeeded(current, now, limits, tuning)
        case Succeeded():
            return _succeeded(current, now)
        case SeriesCleared(since=since):
            return _series_cleared(current, now, since)
        case LimitsObserved(limits=limits):
            return _limits_observed(current, now, limits, tuning)
        case LimitExhausted():
            return _limit_exhausted(current, event, now, tuning)
        case Overloaded():
            return _overloaded(
                current,
                now,
                tuning,
                SignalSource.SERIES,
                StateReason.UNCLASSIFIED_SERIES,
            )
        case ProbeFailed():
            if current.state is not State.HALF_OPEN:
                return _unchanged(current)
            return _overloaded(
                current, now, tuning, SignalSource.PROBE, StateReason.PROBE_FAILED
            )
        case Unauthorized():
            until = now + tuning.auth_refresh_cap_s
            return _escalate(
                current,
                now,
                Entry(
                    State.AUTH_REFRESHING,
                    StateReason.UNAUTHORIZED,
                    SignalSource.PROVIDER_RESPONSE,
                    until,
                ),
            )
        case TokenRevoked():
            return _escalate(
                current,
                now,
                Entry(
                    State.AUTH_FAILED,
                    StateReason.TOKEN_REVOKED,
                    SignalSource.REFRESH,
                    None,
                ),
            )
        case AccountDeactivated():
            return _escalate(
                current,
                now,
                Entry(
                    State.BANNED,
                    StateReason.ACCOUNT_DEACTIVATED,
                    SignalSource.PROVIDER_RESPONSE,
                    None,
                ),
            )
        case _:
            assert_never(event)


def _manual_transition(
    current: StateRecord,
    event: (
        OperatorDisabled | OperatorEnabled | Reauthorized | Restored | RefreshSucceeded
    ),
    now: float,
) -> Transition:
    match event:
        case OperatorDisabled():
            return _enter(
                current,
                now,
                Entry(
                    State.DISABLED,
                    StateReason.OPERATOR,
                    SignalSource.OPERATOR,
                    streak=0,
                ),
            )
        case OperatorEnabled():
            return _resolve(
                current,
                now,
                {State.DISABLED},
                StateReason.OPERATOR,
                SignalSource.OPERATOR,
            )
        case Reauthorized():
            return _resolve(
                current,
                now,
                {State.AUTH_FAILED, State.AUTH_REFRESHING},
                StateReason.OPERATOR,
                SignalSource.OPERATOR,
            )
        case Restored():
            return _resolve(
                current,
                now,
                {State.BANNED},
                StateReason.OPERATOR,
                SignalSource.OPERATOR,
            )
        case RefreshSucceeded():
            return _resolve(
                current,
                now,
                {State.AUTH_REFRESHING},
                StateReason.PROBE_PENDING,
                SignalSource.REFRESH,
            )
        case _:
            assert_never(event)


def exhaustion_until(
    window: LimitWindow, reset_at: float | None, now: float, tuning: ProviderTuning
) -> tuple[float, tuple[Note, ...]]:
    if reset_at is None:
        return (now + tuning.no_reset_block_s, ())
    remaining = reset_at - now
    if remaining <= 0 or remaining > tuning.implausible_reset_max_s:
        return (now + tuning.implausible_block_s, (ImplausibleReset(reset_at),))
    if window is LimitWindow.FIVE_HOUR:
        remaining = min(remaining, tuning.five_hour_block_cap_s)
    return (now + remaining, ())


def soft_limit_until(
    limits: Limits, now: float, tuning: ProviderTuning
) -> float | None:
    windows = (limits.five_hour, limits.weekly)
    exhausted = [
        window
        for window in windows
        if window and window.used_percent >= tuning.soft_threshold_percent
    ]
    if not exhausted:
        return None
    reset_at = max(window.reset_at for window in exhausted)
    remaining = reset_at - now
    if remaining <= 0 or remaining > tuning.implausible_reset_max_s:
        return now + tuning.implausible_block_s
    return reset_at


def _succeeded(current: StateRecord, now: float) -> Transition:
    if current.overload_streak == 0 or current.state not in {
        State.ACTIVE,
        State.SOFT_LIMITED,
        State.OVERLOADED,
    }:
        return _unchanged(current)
    return Transition(
        replace(
            current,
            version=current.version + 1,
            entered_at=current.entered_at,
            overload_streak=0,
        ),
        changed=True,
    )


def _series_cleared(current: StateRecord, now: float, since: float) -> Transition:
    if (
        current.state is not State.OVERLOADED
        or current.source is not SignalSource.SERIES
        or current.entered_at < since
    ):
        return _unchanged(current)
    streak = max(0, current.overload_streak - 1)
    return _enter(
        current,
        now,
        Entry(State.ACTIVE, StateReason.NONE, SignalSource.SERIES, streak=streak),
    )


def _unchanged(current: StateRecord) -> Transition:
    return Transition(current, changed=False)


def _enter(current: StateRecord, now: float, entry: Entry) -> Transition:
    record = StateRecord(
        state=entry.state,
        version=current.version + 1,
        entered_at=now,
        until=entry.until,
        reason=entry.reason,
        source=entry.source,
        overload_streak=(
            current.overload_streak if entry.streak is None else entry.streak
        ),
    )
    return Transition(record, changed=True, notes=entry.notes)


def _resolve(
    current: StateRecord,
    now: float,
    from_states: set[SubscriptionState],
    reason: StateReason,
    source: SignalSource,
) -> Transition:
    if current.state not in from_states:
        return _unchanged(current)
    return _enter(current, now, Entry(State.HALF_OPEN, reason, source, streak=0))


def _expire(current: StateRecord, now: float) -> Transition:
    if current.until is None or now < current.until:
        return _unchanged(current)
    match current.state:
        case State.RATE_LIMITED | State.OVERLOADED | State.AUTH_REFRESHING:
            return _enter(
                current,
                now,
                Entry(State.HALF_OPEN, StateReason.PROBE_PENDING, SignalSource.TIMER),
            )
        case State.SOFT_LIMITED:
            return _enter(
                current,
                now,
                Entry(State.ACTIVE, StateReason.EXPIRED, SignalSource.TIMER, streak=0),
            )
        case _:
            return _unchanged(current)


def _probe_succeeded(
    current: StateRecord, now: float, limits: Limits | None, tuning: ProviderTuning
) -> Transition:
    if current.state not in {State.HALF_OPEN, State.BROKEN}:
        return _unchanged(current)
    soft_until = soft_limit_until(limits, now, tuning) if limits else None
    if soft_until is None:
        return _enter(
            current,
            now,
            Entry(State.ACTIVE, StateReason.NONE, SignalSource.PROBE, streak=0),
        )
    return _enter(
        current,
        now,
        Entry(
            State.SOFT_LIMITED,
            StateReason.SOFT_THRESHOLD,
            SignalSource.PROBE,
            until=soft_until,
            streak=0,
        ),
    )


def _limits_observed(
    current: StateRecord, now: float, limits: Limits, tuning: ProviderTuning
) -> Transition:
    if current.state not in {State.ACTIVE, State.SOFT_LIMITED}:
        return _unchanged(current)
    soft_until = soft_limit_until(limits, now, tuning)
    if soft_until is None:
        if current.state is State.ACTIVE and current.overload_streak == 0:
            return _unchanged(current)
        return _enter(
            current,
            now,
            Entry(
                State.ACTIVE, StateReason.NONE, SignalSource.RESPONSE_HEADERS, streak=0
            ),
        )
    if (
        current.state is State.SOFT_LIMITED
        and current.until is not None
        and abs(soft_until - current.until) <= tuning.soft_until_tolerance_s
        and current.overload_streak == 0
    ):
        return _unchanged(current)
    return _enter(
        current,
        now,
        Entry(
            State.SOFT_LIMITED,
            StateReason.SOFT_THRESHOLD,
            SignalSource.RESPONSE_HEADERS,
            until=soft_until,
            streak=0,
        ),
    )


def _limit_exhausted(
    current: StateRecord, event: LimitExhausted, now: float, tuning: ProviderTuning
) -> Transition:
    until, notes = exhaustion_until(event.window, event.reset_at, now, tuning)
    if current.state is State.RATE_LIMITED:
        if current.until is not None and until <= current.until:
            return _unchanged(current)
        return _enter(
            current,
            now,
            Entry(
                State.RATE_LIMITED,
                StateReason.LIMIT_EXHAUSTED,
                SignalSource.PROVIDER_RESPONSE,
                until=until,
                notes=notes,
            ),
        )
    return _escalate(
        current,
        now,
        Entry(
            State.RATE_LIMITED,
            StateReason.LIMIT_EXHAUSTED,
            SignalSource.PROVIDER_RESPONSE,
            until,
            notes=notes,
        ),
    )


def _overloaded(
    current: StateRecord,
    now: float,
    tuning: ProviderTuning,
    source: SignalSource,
    reason: StateReason,
) -> Transition:
    if (
        current.state is State.OVERLOADED
        or SEVERITY[current.state] > SEVERITY[State.OVERLOADED]
    ):
        return _unchanged(current)
    streak = current.overload_streak + 1
    if streak >= tuning.broken_after:
        return _enter(
            current,
            now,
            Entry(State.BROKEN, StateReason.UNHEALTHY, source, streak=streak),
        )
    pause = min(tuning.overload_base_s * 2 ** (streak - 1), tuning.overload_cap_s)
    return _enter(
        current,
        now,
        Entry(State.OVERLOADED, reason, source, until=now + pause, streak=streak),
    )


def _escalate(current: StateRecord, now: float, entry: Entry) -> Transition:
    if SEVERITY[entry.state] <= SEVERITY[current.state]:
        return _unchanged(current)
    return _enter(current, now, entry)
