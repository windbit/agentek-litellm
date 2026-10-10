from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .model import (
    StateRecord,
    Subscription,
    SubscriptionState,
    UsageRecord,
    Window,
    effective_state,
    is_working,
)
from .providers.chatgpt_profile import Profile


@dataclass(frozen=True, slots=True)
class WindowView:
    used_percent: float
    reset_at: str


@dataclass(frozen=True, slots=True)
class LimitsView:
    five_hour: WindowView | None
    weekly: WindowView | None
    observed_at: str
    source: str


@dataclass(frozen=True, slots=True)
class StateView:
    state: str
    reason: str
    source: str
    until: str | None
    entered_at: str


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    """Everything the operator sees about a subscription; built field by field so no credential can leak into a response."""

    id: str
    provider: str
    name: str
    email: str | None
    plan: str | None
    enabled: bool
    priority: int
    concurrency_limit: int | None
    state: StateView
    limits: LimitsView | None
    in_flight: int

    def as_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderView:
    provider: str
    concurrency_limit: int | None
    subscriptions: int
    working: int

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def utc_iso(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def current_state(
    subscription: Subscription, record: StateRecord | None, now: float
) -> SubscriptionState:
    if not subscription.enabled:
        return SubscriptionState.DISABLED
    return effective_state(record, now) if record else SubscriptionState.ACTIVE


def is_working_now(
    subscription: Subscription, record: StateRecord | None, now: float
) -> bool:
    return is_working(current_state(subscription, record, now))


def subscription_view(
    subscription: Subscription,
    record: StateRecord | None,
    usage: UsageRecord | None,
    profile: Profile,
    now: float,
    *,
    in_flight: int,
) -> SubscriptionView:
    return SubscriptionView(
        id=subscription.id,
        provider=subscription.provider,
        name=subscription.name,
        email=profile.email,
        plan=profile.plan,
        enabled=subscription.enabled,
        priority=subscription.priority,
        concurrency_limit=subscription.concurrency_limit,
        state=_state_view(subscription, record, now),
        limits=_limits_view(usage),
        in_flight=in_flight,
    )


def _state_view(
    subscription: Subscription, record: StateRecord | None, now: float
) -> StateView:
    state = current_state(subscription, record, now)
    if record is None:
        return StateView(state.value, "none", "none", None, utc_iso(now))
    return StateView(
        state=state.value,
        reason=record.reason.value,
        source=record.source.value,
        until=utc_iso(record.until) if record.until is not None else None,
        entered_at=utc_iso(record.entered_at),
    )


def _limits_view(usage: UsageRecord | None) -> LimitsView | None:
    if usage is None:
        return None
    return LimitsView(
        five_hour=_window_view(usage.limits.five_hour),
        weekly=_window_view(usage.limits.weekly),
        observed_at=utc_iso(usage.observed_at),
        source=usage.source.value,
    )


def _window_view(window: Window | None) -> WindowView | None:
    if window is None:
        return None
    return WindowView(window.used_percent, utc_iso(window.reset_at))
