from collections.abc import Mapping
from dataclasses import dataclass

from .compat import StrEnum

SubscriptionId = str


class SubscriptionState(StrEnum):
    ACTIVE = "ACTIVE"
    SOFT_LIMITED = "SOFT_LIMITED"
    RATE_LIMITED = "RATE_LIMITED"
    HALF_OPEN = "HALF_OPEN"
    OVERLOADED = "OVERLOADED"
    BROKEN = "BROKEN"
    AUTH_REFRESHING = "AUTH_REFRESHING"
    AUTH_FAILED = "AUTH_FAILED"
    BANNED = "BANNED"
    DISABLED = "DISABLED"


# A signal replaces the current state only when its target is at least as severe.
# DISABLED is an operator decision and sits outside the order.
SEVERITY: Mapping[SubscriptionState, int] = {
    SubscriptionState.ACTIVE: 0,
    SubscriptionState.HALF_OPEN: 1,
    SubscriptionState.SOFT_LIMITED: 2,
    SubscriptionState.OVERLOADED: 3,
    SubscriptionState.AUTH_REFRESHING: 4,
    SubscriptionState.RATE_LIMITED: 5,
    SubscriptionState.BROKEN: 6,
    SubscriptionState.AUTH_FAILED: 7,
    SubscriptionState.BANNED: 8,
}

WORKING_STATES = frozenset({SubscriptionState.ACTIVE, SubscriptionState.SOFT_LIMITED})
DURABLE_STATES = frozenset(SubscriptionState) - {
    SubscriptionState.ACTIVE,
    SubscriptionState.SOFT_LIMITED,
    SubscriptionState.HALF_OPEN,
}


class StateReason(StrEnum):
    NONE = "none"
    LIMIT_EXHAUSTED = "limit_exhausted"
    SOFT_THRESHOLD = "soft_threshold"
    UNCLASSIFIED_SERIES = "unclassified_series"
    PROBE_FAILED = "probe_failed"
    UNHEALTHY = "unhealthy"
    UNAUTHORIZED = "unauthorized"
    TOKEN_REVOKED = "token_revoked"
    ACCOUNT_DEACTIVATED = "account_deactivated"
    OPERATOR = "operator"
    PROBE_PENDING = "probe_pending"
    EXPIRED = "expired"


class SignalSource(StrEnum):
    NONE = "none"
    PROVIDER_RESPONSE = "provider_response"
    RESPONSE_HEADERS = "response_headers"
    SERIES = "series"
    PROBE = "probe"
    REFRESH = "refresh"
    OPERATOR = "operator"
    TIMER = "timer"


@dataclass(frozen=True, slots=True)
class StateRecord:
    state: SubscriptionState
    version: int
    entered_at: float
    until: float | None
    reason: StateReason
    source: SignalSource
    overload_streak: int


def initial_record(now: float) -> StateRecord:
    return StateRecord(
        state=SubscriptionState.ACTIVE,
        version=0,
        entered_at=now,
        until=None,
        reason=StateReason.NONE,
        source=SignalSource.NONE,
        overload_streak=0,
    )


def effective_state(record: StateRecord, now: float) -> SubscriptionState:
    if record.until is None or now < record.until:
        return record.state
    match record.state:
        case (
            SubscriptionState.RATE_LIMITED
            | SubscriptionState.OVERLOADED
            | SubscriptionState.AUTH_REFRESHING
        ):
            return SubscriptionState.HALF_OPEN
        case SubscriptionState.SOFT_LIMITED:
            return SubscriptionState.ACTIVE
        case _:
            return record.state


def is_working(state: SubscriptionState) -> bool:
    return state in WORKING_STATES


@dataclass(frozen=True, slots=True)
class Subscription:
    id: SubscriptionId
    provider: str
    name: str
    credential_name: str
    priority: int = 50
    concurrency_limit: int | None = None
    egress: str | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Route:
    provider: str
    egress: str | None


@dataclass(frozen=True, slots=True)
class Window:
    used_percent: float
    reset_at: float


@dataclass(frozen=True, slots=True)
class Limits:
    five_hour: Window | None = None
    weekly: Window | None = None


@dataclass(frozen=True, slots=True)
class UsageRecord:
    limits: Limits
    observed_at: float
