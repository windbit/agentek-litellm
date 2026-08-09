from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Union

import litellm
from litellm.litellm_core_utils.duration_parser import get_next_standardized_reset_time

if TYPE_CHECKING:
    from litellm.models.team import BudgetLimitEntry


def get_budget_reset_timezone():
    """
    Get the budget reset timezone from litellm_settings.
    Falls back to UTC if not specified.

    litellm_settings values are set as attributes on the litellm module
    by proxy_server.py at startup (via setattr(litellm, key, value)).
    """
    return getattr(litellm, "timezone", None) or "UTC"


def _parse_anchor(anchor: Optional[Union[str, datetime]]) -> Optional[datetime]:
    if anchor is None or isinstance(anchor, datetime):
        return anchor
    return datetime.fromisoformat(anchor.replace("Z", "+00:00"))


def get_budget_reset_time(
    budget_duration: str, anchor: Optional[Union[str, datetime]] = None
) -> datetime:
    """
    Get the budget reset time based on the configured timezone.
    Falls back to UTC if not specified.

    When anchor (billing period start) is given, the window resets relative to
    it (same day-of-month / weekday) instead of snapping to calendar boundaries.
    """

    reset_at = get_next_standardized_reset_time(
        duration=budget_duration,
        current_time=datetime.now(timezone.utc),
        timezone_str=get_budget_reset_timezone(),
        anchor=_parse_anchor(anchor),
    )
    return reset_at


def initialize_budget_windows(
    windows: List[Union[dict, "BudgetLimitEntry"]],
) -> List[dict]:
    """
    Set reset_at on each budget window from its duration and optional anchor.

    Accepts dicts or Pydantic BudgetLimitEntry values; returns plain JSON-safe
    dicts with reset_at (and, when present, a normalized anchor) as ISO strings.
    """
    return [_initialize_window(w) for w in windows]


def _initialize_window(window: Union[dict, "BudgetLimitEntry"]) -> dict:
    w = dict(window) if isinstance(window, dict) else window.model_dump()
    anchor = _parse_anchor(w.get("anchor"))
    reset_at = get_budget_reset_time(
        budget_duration=w["budget_duration"], anchor=anchor
    )
    initialized = {**w, "reset_at": reset_at.isoformat()}
    _set_or_drop(initialized, "anchor", anchor)
    # Windows are persisted with json.dumps, so a datetime off the model has to go out as a string.
    _set_or_drop(initialized, "spend_since", _parse_anchor(w.get("spend_since")))
    return initialized


def _set_or_drop(window: dict, key: str, value: Optional[datetime]) -> None:
    if value is None:
        window.pop(key, None)
    else:
        window[key] = value.isoformat()
