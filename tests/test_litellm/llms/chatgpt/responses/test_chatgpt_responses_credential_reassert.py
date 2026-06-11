"""
Regression tests for the post-merge credential header reassertion in the
ChatGPT Responses API transform (issue #230, reviewer finding on
_reassert_credential_headers).

The shared HTTP handler merges user-supplied ``extra_headers`` over the headers
produced by ``validate_environment``. ``_reassert_credential_headers`` is the
last hook before the request is sent and must guarantee that no user-supplied
``Authorization`` / ``ChatGPT-Account-Id`` (in any case variant) can reach the
wire. The previous implementation:

  1. resolved the access token via a fresh Authenticator (a SECOND refresh that
     fails for rotating refresh tokens), and
  2. swallowed GetAccessTokenError, returning BEFORE stripping attacker headers.

Source: litellm/llms/chatgpt/responses/transformation.py
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from litellm.exceptions import AuthenticationError
from litellm.llms.chatgpt.common_utils import GetAccessTokenError
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig
from litellm.types.router import GenericLiteLLMParams


def _credential_params():
    return GenericLiteLLMParams(
        litellm_credential_name="chatgpt_acct_a",
        chatgpt_auth={
            "access_token": "tok-a",
            "account_id": "acct-a",
            "expires_at": time.time() + 3600,
        },
    )


def test_get_authenticator_caches_per_request():
    """validate_environment and the later reassert must share one authenticator
    so the token resolved (and possibly refreshed) during validate is reused
    rather than refreshed a second time."""
    config = ChatGPTResponsesAPIConfig()
    litellm_params = _credential_params()

    with patch(
        "litellm.llms.chatgpt.responses.transformation.Authenticator.from_litellm_params"
    ) as mock_from:
        mock_from.return_value = MagicMock()
        first = config._get_authenticator(litellm_params)
        second = config._get_authenticator(litellm_params)

    assert first is second
    assert mock_from.call_count == 1


def test_reassert_reuses_authenticator_without_second_refresh():
    """The reassert must call the cached authenticator from validate (no new
    Authenticator), so a single refresh is shared across both calls."""
    config = ChatGPTResponsesAPIConfig()
    litellm_params = _credential_params()

    authenticator = MagicMock()
    authenticator.get_access_token.return_value = "tok-a"
    authenticator.get_account_id.return_value = "acct-a"

    with patch(
        "litellm.llms.chatgpt.responses.transformation.Authenticator.from_litellm_params",
        return_value=authenticator,
    ) as mock_from:
        # Simulate validate_environment resolving (and caching) the authenticator.
        config._get_authenticator(litellm_params)
        headers = {
            "Authorization": "Bearer attacker-exact",
            "authorization": "Bearer attacker-lower",
            "CHATGPT-ACCOUNT-ID": "acct-attacker",
            "x-trace": "t1",
        }
        config._reassert_credential_headers(headers, "gpt-5.4", litellm_params)

    # Only one Authenticator was ever built for this request.
    assert mock_from.call_count == 1
    assert headers["Authorization"] == "Bearer tok-a"
    assert headers["ChatGPT-Account-Id"] == "acct-a"
    assert "authorization" not in headers
    assert "CHATGPT-ACCOUNT-ID" not in headers
    assert headers["x-trace"] == "t1"


def test_reassert_fails_closed_and_strips_attacker_headers():
    """If the credential cannot be resolved during reassert, we must NOT silently
    return leaving attacker-supplied auth headers on the request. We strip first,
    then fail closed."""
    config = ChatGPTResponsesAPIConfig()
    litellm_params = _credential_params()

    failing = MagicMock()
    failing.get_access_token.side_effect = GetAccessTokenError(
        message="token refresh failed", status_code=401
    )

    headers = {
        "Authorization": "Bearer attacker-exact",
        "authorization": "Bearer attacker-lower",
        "ChatGPT-Account-Id": "acct-attacker",
        "chatgpt-account-id": "acct-attacker-lower",
        "x-trace": "t1",
    }

    with patch.object(config, "_get_authenticator", return_value=failing):
        with pytest.raises(AuthenticationError):
            config._reassert_credential_headers(headers, "gpt-5.4", litellm_params)

    # No case variant of the protected headers survived, even on failure.
    assert not any(
        key.lower() in ("authorization", "chatgpt-account-id") for key in headers
    )
    # Unrelated user headers are untouched.
    assert headers["x-trace"] == "t1"
