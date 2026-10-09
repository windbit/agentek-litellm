import base64
import json
import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

from litellm.llms.chatgpt.authenticator import (
    Authenticator,
    refresh_chatgpt_credential_values,
)


def _make_jwt(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def _b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{_b64(header)}.{_b64(payload)}."


class TestChatGPTAuthenticator:
    @pytest.fixture
    def authenticator(self):
        with patch("os.path.exists", return_value=True):
            return Authenticator()

    def test_get_access_token_from_file(self, authenticator):
        future_time = time.time() + 3600
        auth_data = json.dumps({"access_token": "token-123", "expires_at": future_time})

        with patch("builtins.open", mock_open(read_data=auth_data)):
            token = authenticator.get_access_token()
            assert token == "token-123"

    def test_get_access_token_refresh(self, authenticator):
        past_time = time.time() - 10
        auth_data = json.dumps(
            {
                "access_token": "token-old",
                "refresh_token": "refresh-123",
                "expires_at": past_time,
            }
        )
        refreshed = {
            "access_token": "token-new",
            "refresh_token": "refresh-123",
            "id_token": "id-123",
        }

        with (
            patch("builtins.open", mock_open(read_data=auth_data)),
            patch.object(authenticator, "_refresh_tokens", return_value=refreshed),
        ):
            token = authenticator.get_access_token()
            assert token == "token-new"

    def test_get_account_id_from_id_token(self, authenticator):
        id_token = _make_jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"}}
        )
        auth_data = json.dumps({"id_token": id_token})

        with (
            patch("builtins.open", mock_open(read_data=auth_data)),
            patch.object(authenticator, "_write_auth_file") as mock_write,
        ):
            account_id = authenticator.get_account_id()
            assert account_id == "acct-123"
            mock_write.assert_called_once()
            assert mock_write.call_args[0][0]["account_id"] == "acct-123"


class TestRefreshChatgptCredentialValues:
    @staticmethod
    def _http_client(tokens: dict) -> MagicMock:
        client = MagicMock()
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = tokens
        client.post.return_value = response
        return client

    def test_none_when_no_chatgpt_auth(self):
        assert refresh_chatgpt_credential_values({"other": "x"}, 600) is None

    def test_none_when_token_fresh(self):
        values = {
            "chatgpt_auth": {
                "access_token": "old",
                "refresh_token": "r1",
                "expires_at": time.time() + 10000,
            }
        }
        with patch(
            "litellm.llms.chatgpt.authenticator._get_httpx_client"
        ) as mock_client:
            assert refresh_chatgpt_credential_values(values, 600) is None
            mock_client.assert_not_called()

    def test_refreshes_when_expiring(self):
        new_access = _make_jwt({"exp": int(time.time()) + 3600})
        tokens = {
            "access_token": new_access,
            "id_token": "id-new",
            "refresh_token": "r2",
        }
        values = {
            "chatgpt_token_dir": "/keep",
            "chatgpt_auth": {
                "access_token": "old",
                "refresh_token": "r1",
                "expires_at": time.time() + 100,
            },
        }
        with patch(
            "litellm.llms.chatgpt.authenticator._get_httpx_client",
            return_value=self._http_client(tokens),
        ):
            result = refresh_chatgpt_credential_values(values, 600)

        assert result is not None
        assert result["chatgpt_token_dir"] == "/keep"
        assert result["chatgpt_auth"]["access_token"] == new_access
        assert result["chatgpt_auth"]["refresh_token"] == "r2"
        assert result["chatgpt_auth"]["expires_at"] > time.time()

    def test_none_on_refresh_failure(self):
        client = MagicMock()
        client.post.side_effect = Exception("boom")
        values = {
            "chatgpt_auth": {
                "access_token": "old",
                "refresh_token": "r1",
                "expires_at": time.time() + 100,
            }
        }
        with patch(
            "litellm.llms.chatgpt.authenticator._get_httpx_client",
            return_value=client,
        ):
            assert refresh_chatgpt_credential_values(values, 600) is None


class TestRefreshGuard:
    """REFRESH_GUARD lets a gateway plugin serialize refreshes of a named credential."""

    @staticmethod
    def _expired_inline_authenticator(credential_name):
        return Authenticator(
            auth_inline={
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": time.time() - 10,
            },
            credential_required=True,
            credential_name=credential_name,
        )

    @pytest.fixture(autouse=True)
    def _reset_guard(self):
        from litellm.llms.chatgpt import authenticator as authenticator_module

        yield authenticator_module
        authenticator_module.REFRESH_GUARD = None

    def test_guard_receives_the_stale_refresh_token_and_its_tokens_are_used(
        self, _reset_guard
    ):
        calls = []

        def guard(credential_name, stale_refresh_token, refresh):
            calls.append((credential_name, stale_refresh_token))
            return {
                "access_token": "guarded-access",
                "refresh_token": "guarded-refresh",
                "id_token": "guarded-id",
            }

        _reset_guard.REFRESH_GUARD = guard
        authenticator = self._expired_inline_authenticator("cred-a")

        token = authenticator.get_access_token()

        assert (token, calls, authenticator._auth_inline["refresh_token"]) == (
            "guarded-access",
            [("cred-a", "old-refresh")],
            "guarded-refresh",
        )

    def test_guard_is_skipped_without_a_credential_name(self, _reset_guard):
        _reset_guard.REFRESH_GUARD = MagicMock(side_effect=AssertionError("guard used"))
        authenticator = self._expired_inline_authenticator(None)
        refreshed = {
            "access_token": "direct-access",
            "refresh_token": "direct-refresh",
            "id_token": "direct-id",
        }

        with patch.object(
            Authenticator, "_request_refreshed_tokens", return_value=refreshed
        ):
            assert authenticator.get_access_token() == "direct-access"

    def test_guard_can_run_the_network_refresh_itself(self, _reset_guard):
        refreshed = {
            "access_token": "network-access",
            "refresh_token": "network-refresh",
            "id_token": "network-id",
        }
        _reset_guard.REFRESH_GUARD = (
            lambda credential_name, stale_refresh_token, refresh: refresh()
        )
        authenticator = self._expired_inline_authenticator("cred-a")

        with patch.object(
            Authenticator, "_request_refreshed_tokens", return_value=refreshed
        ) as request:
            token = authenticator.get_access_token()

        assert (token, request.call_args.args) == ("network-access", ("old-refresh",))
