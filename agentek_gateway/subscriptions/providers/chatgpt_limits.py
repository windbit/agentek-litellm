from collections.abc import Mapping

from ..events import LimitWindow
from ..model import Limits, Window
from .base import Headers
from .chatgpt_json import error_object, mapping, number_of

HEADER_PREFIX = "llm_provider-"

FIVE_HOUR_MAX_WINDOW_MINUTES = 24 * 60

FULL_USAGE_PERCENT = 100.0

USAGE_LIMIT_MARKER = "usage_limit_reached"


def normalize_headers(headers: Headers) -> dict[str, str]:
    return {
        name.lower().removeprefix(HEADER_PREFIX): value
        for name, value in headers.items()
    }


def parse_limits(
    headers: Headers, body: Mapping[str, object] | None, *, now: float
) -> Limits | None:
    normalized = normalize_headers(headers)
    primary = header_window(normalized, "primary", now)
    secondary = header_window(normalized, "secondary", now)
    five_hour = weekly = None
    for label, (window, minutes) in (("primary", primary), ("secondary", secondary)):
        if window is None:
            continue
        slot = slot_for(label, minutes, secondary_present=secondary[0] is not None)
        if slot is LimitWindow.FIVE_HOUR:
            five_hour = window
        else:
            weekly = window
    if five_hour or weekly:
        return Limits(five_hour=five_hour, weekly=weekly)
    return limits_from_error_body(body, now)


def header_window(
    headers: Mapping[str, str], label: str, now: float
) -> tuple[Window | None, float]:
    used = number_of(headers.get(f"x-codex-{label}-used-percent"))
    minutes = number_of(headers.get(f"x-codex-{label}-window-minutes")) or 0.0
    reset_at = number_of(headers.get(f"x-codex-{label}-reset-at"))
    after = number_of(headers.get(f"x-codex-{label}-reset-after-seconds"))
    if reset_at is None and after:
        reset_at = now + after
    if used is None or reset_at is None:
        return None, minutes
    return Window(used, reset_at), minutes


def slot_for(label: str, minutes: float, *, secondary_present: bool) -> LimitWindow:
    if minutes > 0:
        return (
            LimitWindow.FIVE_HOUR
            if minutes <= FIVE_HOUR_MAX_WINDOW_MINUTES
            else LimitWindow.WEEKLY
        )
    if label == "primary" and secondary_present:
        return LimitWindow.FIVE_HOUR
    return LimitWindow.WEEKLY


def limits_from_error_body(
    body: Mapping[str, object] | None, now: float
) -> Limits | None:
    error = error_object(body)
    if not error or USAGE_LIMIT_MARKER not in (error.get("type"), error.get("code")):
        return None
    window, reset_at = limit_window_and_reset({}, error, now)
    if reset_at is None:
        return None
    exhausted = Window(FULL_USAGE_PERCENT, reset_at)
    return (
        Limits(five_hour=exhausted)
        if window is LimitWindow.FIVE_HOUR
        else Limits(weekly=exhausted)
    )


def limit_window_and_reset(
    headers: Headers, error: Mapping[str, object], now: float
) -> tuple[LimitWindow, float | None]:
    minutes = number_of(error.get("limit_window_minutes"))
    reset_at = number_of(error.get("resets_at"))
    if reset_at is None:
        after = number_of(error.get("resets_in_seconds"))
        reset_at = now + after if after is not None else None
    limits = parse_limits(headers, None, now=now)
    exhausted = exhausted_window(limits)
    if reset_at is None and exhausted:
        reset_at = exhausted[1].reset_at
    return window_kind(minutes, exhausted[0] if exhausted else None), reset_at


def exhausted_window(limits: Limits | None) -> tuple[LimitWindow, Window] | None:
    if limits is None:
        return None
    for kind, window in (
        (LimitWindow.WEEKLY, limits.weekly),
        (LimitWindow.FIVE_HOUR, limits.five_hour),
    ):
        if window and window.used_percent >= FULL_USAGE_PERCENT:
            return kind, window
    return None


def window_kind(minutes: float | None, from_headers: LimitWindow | None) -> LimitWindow:
    if minutes:
        return (
            LimitWindow.FIVE_HOUR
            if minutes <= FIVE_HOUR_MAX_WINDOW_MINUTES
            else LimitWindow.WEEKLY
        )
    return from_headers or LimitWindow.UNKNOWN


def limits_from_usage_payload(
    payload: Mapping[str, object] | None, now: float
) -> Limits | None:
    rate_limit = mapping(payload.get("rate_limit")) if payload else None
    if not rate_limit:
        return None
    five_hour = weekly = None
    for key in ("primary_window", "secondary_window"):
        raw = mapping(rate_limit.get(key))
        window = usage_window(raw, now) if raw else None
        if window is None or raw is None:
            continue
        seconds = number_of(raw.get("limit_window_seconds"))
        if seconds is not None and seconds <= 0:
            continue
        if seconds is not None and seconds / 60 <= FIVE_HOUR_MAX_WINDOW_MINUTES:
            five_hour = window
        else:
            weekly = window
    return Limits(five_hour=five_hour, weekly=weekly) if (five_hour or weekly) else None


def usage_window(raw: Mapping[str, object], now: float) -> Window | None:
    used = number_of(raw.get("used_percent"))
    reset_at = number_of(raw.get("reset_at"))
    after = number_of(raw.get("reset_after_seconds"))
    if reset_at is None and after is not None:
        reset_at = now + after
    if used is None or reset_at is None:
        return None
    return Window(used, reset_at)
