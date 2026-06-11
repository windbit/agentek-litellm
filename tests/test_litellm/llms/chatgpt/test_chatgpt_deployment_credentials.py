"""
Tests for per-deployment ChatGPT subscription credential resolution.

One ChatGPT subscription/account maps to one LiteLLM credential, selected per
deployment via ``litellm_credential_name`` or ``chatgpt_auth_file`` /
``chatgpt_token_dir`` pointing at that account's auth.json.

Source:
- litellm/llms/chatgpt/common_utils.py (resolution helpers)
- litellm/llms/chatgpt/authenticator.py (Authenticator.from_litellm_params)
"""

import base64
import json
import time
from unittest.mock import patch

import httpx
import pytest

from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.llms.chatgpt.common_utils import (
    GetAccessTokenError,
    chatgpt_credential_requested,
    merge_chatgpt_request_headers,
    resolve_chatgpt_deployment_credential,
)


def _make_jwt(payload: dict) -> str:
    def _b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{_b64({'alg': 'none'})}.{_b64(payload)}."


def _write_auth_file(tmp_path, auth: dict) -> str:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    return str(path)


class TestResolveDeploymentCredential:
    def test_auth_file_is_requested(self, tmp_path):
        path = _write_auth_file(tmp_path, {"access_token": "tok"})
        params = {
            "chatgpt_auth_file": path,
            "chatgpt_api_base": "https://acct-a.example.com",
        }
        cred = resolve_chatgpt_deployment_credential(params)
        assert cred["requested"] is True
        assert cred["auth_file"] == path
        assert cred["api_base"] == "https://acct-a.example.com"
        assert chatgpt_credential_requested(params) is True

    def test_token_dir_is_requested(self):
        params = {"chatgpt_token_dir": "/tmp/acct-a"}
        cred = resolve_chatgpt_deployment_credential(params)
        assert cred["requested"] is True
        assert cred["token_dir"] == "/tmp/acct-a"

    def test_credential_name_only_is_requested(self):
        params = {"litellm_credential_name": "chatgpt_acct_a"}
        cred = resolve_chatgpt_deployment_credential(params)
        assert cred["requested"] is True

    def test_empty_params_not_requested(self):
        assert chatgpt_credential_requested({}) is False
        assert resolve_chatgpt_deployment_credential({})["requested"] is False
        assert chatgpt_credential_requested(None) is False


class TestAuthenticatorFromLitellmParams:
    def test_valid_token_from_auth_file(self, tmp_path):
        future = time.time() + 3600
        path = _write_auth_file(
            tmp_path, {"access_token": "tok-a", "expires_at": future}
        )
        params = {
            "chatgpt_auth_file": path,
            "chatgpt_api_base": "https://acct-a.example.com",
        }
        auth = Authenticator.from_litellm_params(params)
        assert auth.get_access_token() == "tok-a"
        assert auth.get_api_base() == "https://acct-a.example.com"

    def test_account_id_derived_from_id_token(self, tmp_path):
        future = time.time() + 3600
        id_token = _make_jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-xyz"}}
        )
        path = _write_auth_file(
            tmp_path,
            {"access_token": "tok-a", "expires_at": future, "id_token": id_token},
        )
        auth = Authenticator.from_litellm_params({"chatgpt_auth_file": path})
        assert auth.get_account_id() == "acct-xyz"

    def test_requested_but_missing_credential_fails_no_fallback(self):
        """litellm_credential_name set but credential resolved to no auth file ->
        explicit fail. Must NOT read the global auth file or trigger device login.
        """
        auth = Authenticator.from_litellm_params(
            {"litellm_credential_name": "chatgpt_acct_a"}
        )
        assert auth.auth_file is None
        assert auth.token_dir is None
        with patch.object(auth, "_login_device_code") as mock_login:
            with pytest.raises(GetAccessTokenError):
                auth.get_access_token()
            mock_login.assert_not_called()

    def test_expired_token_refreshes_and_persists_rotated_tokens(self, tmp_path):
        """An expired access token is refreshed and the rotated refresh token is
        written back to the auth file, so the next request does not reuse a stale
        (already-invalidated) refresh token.
        """
        past = time.time() - 10
        path = _write_auth_file(
            tmp_path,
            {
                "access_token": "tok-old",
                "refresh_token": "ref-old",
                "expires_at": past,
            },
        )
        auth = Authenticator.from_litellm_params({"chatgpt_auth_file": path})

        id_token = _make_jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"}}
        )

        class DummyClient:
            def post(self, *args, **kwargs):
                request = httpx.Request("POST", "https://auth.openai.com/oauth/token")
                return httpx.Response(
                    200,
                    json={
                        "access_token": "tok-new",
                        "refresh_token": "ref-new",
                        "id_token": id_token,
                    },
                    request=request,
                )

        with patch(
            "litellm.llms.chatgpt.authenticator._get_httpx_client",
            return_value=DummyClient(),
        ):
            assert auth.get_access_token() == "tok-new"

        persisted = json.loads(open(path).read())
        assert persisted["access_token"] == "tok-new"
        assert persisted["refresh_token"] == "ref-new"

    def test_expired_token_refresh_failure_fails(self, tmp_path):
        from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

        past = time.time() - 10
        path = _write_auth_file(
            tmp_path,
            {"access_token": "tok-old", "refresh_token": "ref-a", "expires_at": past},
        )
        auth = Authenticator.from_litellm_params({"chatgpt_auth_file": path})
        with patch.object(
            auth,
            "_refresh_tokens",
            side_effect=RefreshAccessTokenError(message="boom", status_code=401),
        ):
            with pytest.raises(GetAccessTokenError):
                auth.get_access_token()

    def test_refresh_failure_error_does_not_leak_token_response_values(self, tmp_path):
        """Malformed OAuth refresh responses must not surface raw token values."""

        class DummyClient:
            def post(self, *args, **kwargs):
                request = httpx.Request("POST", "https://auth.openai.com/oauth/token")
                return httpx.Response(
                    200,
                    json={
                        "access_token": "LEAK_ME_ACCESS_TOKEN",
                        "refresh_token": "LEAK_ME_REFRESH_TOKEN",
                    },
                    request=request,
                )

        past = time.time() - 10
        path = _write_auth_file(
            tmp_path,
            {
                "access_token": "old-token",
                "refresh_token": "old-refresh-token",
                "expires_at": past,
            },
        )
        auth = Authenticator.from_litellm_params(
            {"litellm_credential_name": "chatgpt_acct_a", "chatgpt_auth_file": path}
        )

        with patch(
            "litellm.llms.chatgpt.authenticator._get_httpx_client",
            return_value=DummyClient(),
        ):
            with pytest.raises(GetAccessTokenError) as exc_info:
                auth.get_access_token()

        message = str(exc_info.value)
        assert "LEAK_ME_ACCESS_TOKEN" not in message
        assert "LEAK_ME_REFRESH_TOKEN" not in message
        assert "old-refresh-token" not in message

    def test_refresh_failure_error_does_not_leak_id_or_request_refresh_token(
        self, tmp_path
    ):
        """Malformed refresh errors must not leak id_token or request tokens."""

        class DummyClient:
            def post(self, *args, **kwargs):
                request = httpx.Request("POST", "https://auth.openai.com/oauth/token")
                return httpx.Response(
                    200,
                    json={
                        "refresh_token": "LEAK_ME_REFRESH_TOKEN",
                        "id_token": "LEAK_ME_ID_TOKEN",
                    },
                    request=request,
                )

        past = time.time() - 10
        path = _write_auth_file(
            tmp_path,
            {
                "access_token": "old-token",
                "refresh_token": "REQUEST_REFRESH_TOKEN_SECRET",
                "expires_at": past,
            },
        )
        auth = Authenticator.from_litellm_params(
            {"litellm_credential_name": "chatgpt_acct_a", "chatgpt_auth_file": path}
        )

        with patch(
            "litellm.llms.chatgpt.authenticator._get_httpx_client",
            return_value=DummyClient(),
        ):
            with pytest.raises(GetAccessTokenError) as exc_info:
                auth.get_access_token()

        message = str(exc_info.value)
        assert "LEAK_ME_REFRESH_TOKEN" not in message
        assert "LEAK_ME_ID_TOKEN" not in message
        assert "REQUEST_REFRESH_TOKEN_SECRET" not in message

    def test_not_requested_returns_global_authenticator(self):
        auth = Authenticator.from_litellm_params({})
        assert auth.auth_file is not None


class TestMergeChatGPTRequestHeaders:
    def test_user_headers_cannot_override_credential_auth(self):
        credential_headers = {
            "Authorization": "Bearer real-token",
            "ChatGPT-Account-Id": "acct-real",
            "originator": "codex_cli_rs",
        }
        user_headers = {
            "Authorization": "Bearer attacker",
            "ChatGPT-Account-Id": "acct-attacker",
            "originator": "custom-origin",
            "x-trace": "abc",
        }
        merged = merge_chatgpt_request_headers(credential_headers, user_headers)
        assert merged["Authorization"] == "Bearer real-token"
        assert merged["ChatGPT-Account-Id"] == "acct-real"
        # non-auth user headers are still honored
        assert merged["originator"] == "custom-origin"
        assert merged["x-trace"] == "abc"

    def test_user_headers_cannot_override_credential_auth_case_insensitively(self):
        credential_headers = {
            "Authorization": "Bearer real-token",
            "ChatGPT-Account-Id": "acct-real",
        }
        user_headers = {
            "authorization": "Bearer attacker-lower",
            "CHATGPT-ACCOUNT-ID": "acct-attacker-upper",
            "x-trace": "abc",
        }

        merged = merge_chatgpt_request_headers(credential_headers, user_headers)

        assert merged["Authorization"] == "Bearer real-token"
        assert merged["ChatGPT-Account-Id"] == "acct-real"
        assert "authorization" not in merged
        assert "CHATGPT-ACCOUNT-ID" not in merged
        assert merged["x-trace"] == "abc"
