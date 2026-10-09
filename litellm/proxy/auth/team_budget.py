from dataclasses import dataclass
from datetime import datetime
from math import isfinite
import logging
from typing import Literal, Optional

from pydantic import BaseModel

from litellm.models.team import BudgetLimitEntry
from litellm.proxy._types import LiteLLM_TeamTable
from litellm.proxy.spend_tracking.budget_reservation import get_budget_window_start


class ExceededWindow(BaseModel):
    duration: str
    resetAt: Optional[datetime] = None


class BudgetState(BaseModel):
    status: Literal["available", "exceeded", "unknown"]
    windows: tuple[ExceededWindow, ...] = ()


@dataclass(frozen=True, slots=True)
class WindowReading:
    window: BudgetLimitEntry
    spend: float
    reliable: bool
    error: Optional[Exception] = None

    @property
    def exceeded(self) -> bool:
        return isfinite(self.window.max_budget) and self.spend >= self.window.max_budget


async def team_budget_readings(team: LiteLLM_TeamTable) -> tuple[WindowReading, ...]:
    return tuple([await _safe_window_reading(team.team_id, BudgetLimitEntry.model_validate(
        window if isinstance(window, dict) else window.model_dump()
    )) for window in team.budget_limits or ()])


async def _window_reading(team_id: Optional[str], window: BudgetLimitEntry) -> WindowReading:
    from litellm.proxy.proxy_server import (
        _ensure_window_spend_counter_initialized,
        get_current_spend_reading,
    )

    counter_key = f"spend:team:{team_id}:window:{window.budget_duration}"
    window_start = get_budget_window_start(window)
    if window_start is not None and team_id is not None:
        await _ensure_window_spend_counter_initialized(counter_key, "Team", team_id, window_start)
    spend, reliable = await get_current_spend_reading(counter_key, 0.0)
    return WindowReading(window, spend, reliable)


def budget_state(readings: tuple[WindowReading, ...]) -> BudgetState:
    exceeded = tuple(
        ExceededWindow(duration=reading.window.budget_duration, resetAt=reading.window.reset_at)
        for reading in readings if reading.exceeded and reading.reliable
    )
    if exceeded:
        return BudgetState(status="exceeded", windows=exceeded)
    status = "available" if all(reading.reliable for reading in readings) else "unknown"
    return BudgetState(status=status)


def rejected_budget_state(readings: tuple[WindowReading, ...]) -> BudgetState:
    return BudgetState(status="exceeded", windows=tuple(
        ExceededWindow(duration=reading.window.budget_duration, resetAt=reading.window.reset_at)
        for reading in readings if reading.exceeded
    ))


async def _safe_window_reading(team_id: Optional[str], window: BudgetLimitEntry) -> WindowReading:
    try:
        return await _window_reading(team_id, window)
    except Exception as error:
        logging.getLogger(__name__).warning("Team budget window unavailable: %s", window.budget_duration)
        return WindowReading(window, 0.0, False, error)


def budget_failure_fields(team: LiteLLM_TeamTable, state: BudgetState) -> dict[str, object]:
    return {"account_budget": {
        **state.model_dump(mode="json", exclude_none=True),
        "context": (team.metadata or {}).get("budget_context", {}),
    }}
