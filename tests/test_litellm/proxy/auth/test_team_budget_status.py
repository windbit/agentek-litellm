from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from litellm.proxy._types import LiteLLM_TeamTable
from litellm.proxy.auth.auth_checks import _team_multi_budget_check
from litellm.proxy.auth.team_budget import budget_state, team_budget_readings
from litellm.exceptions import BudgetExceededError


def team() -> LiteLLM_TeamTable:
    return LiteLLM_TeamTable(
        team_id="budget-status-test",
        metadata={"budget_context": {"hideSaasFeatures": True}},
        budget_limits=[
            {"budget_duration": "1d", "max_budget": 5, "reset_at": datetime(2026, 10, 10, tzinfo=timezone.utc)},
            {"budget_duration": "1mo", "max_budget": 10, "reset_at": datetime(2026, 11, 1, tzinfo=timezone.utc)},
        ],
    )


@pytest.mark.asyncio
async def test_unknown_reading_cannot_confirm_available():
    with patch("litellm.proxy.proxy_server.get_current_spend_reading", new=AsyncMock(return_value=(0.0, False))):
        state = budget_state(await team_budget_readings(team()))
    assert state.status == "unknown"


@pytest.mark.asyncio
async def test_both_windows_survive_into_budget_exception():
    with patch("litellm.proxy.proxy_server.get_current_spend_reading", new=AsyncMock(return_value=(12.0, True))):
        with pytest.raises(BudgetExceededError) as raised:
            await _team_multi_budget_check(team())
    budget = raised.value.provider_specific_fields["account_budget"]
    assert budget["status"] == "exceeded"
    assert [window["duration"] for window in budget["windows"]] == ["1d", "1mo"]
    assert all(window["resetAt"] for window in budget["windows"])
    assert budget["context"]["hideSaasFeatures"] is True


@pytest.mark.asyncio
async def test_failed_second_window_cannot_hide_confirmed_daily_limit():
    with patch("litellm.proxy.proxy_server.get_current_spend_reading", new=AsyncMock(side_effect=[(6.0, True), OSError("redis unavailable")])):
        readings = await team_budget_readings(team())
    assert budget_state(readings).status == "exceeded"
    assert budget_state(readings).windows[0].duration == "1d"
