"""
Tests for per-deployment ChatGPT subscription credential resolution.

Covers issue #230: one ChatGPT subscription/account == one LiteLLM credential,
selected per deployment via ``litellm_credential_name`` / inline ``chatgpt_auth``.

Source:
- litellm/llms/chatgpt/common_utils.py (resolution helpers)
- litellm/llms/chatgpt/authenticator.py (Authenticator.from_litellm_params)
"""

import base64
import json
import time
from unittest.mock import patch

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


class TestResolveDeploymentCredential:
    def test_nested_chatgpt_auth_is_requested(self):
        params = {
            "chatgpt_auth": {"access_token": "tok", "refresh_token": "ref"},
            "chatgpt_api_base": "https://acct-a.example.com",
        }
        cred = resolve_chatgpt_deployment_credential(params)
        assert cred["requested"] is True
        assert cred["inline_auth"] == {"access_token": "tok", "refresh_token": "ref"}
        assert cred["api_base"] == "https://acct-a.example.com"
        assert chatgpt_credential_requested(params) is True

    def test_flat_fields_are_requested(self):
        params = {"access_token": "tok", "account_id": "acct-1"}
        cred = resolve_chatgpt_deployment_credential(params)
        assert cred["requested"] is True
        assert cred["inline_auth"]["access_token"] == "tok"
        assert cred["inline_auth"]["account_id"] == "acct-1"

    def test_token_dir_is_requested(self):
        params = {"chatgpt_token_dir": "/tmp/acct-a"}
        cred = resolve_chatgpt_deployment_credential(params)
        assert cred["requested"] is True
        assert cred["token_dir"] == "/tmp/acct-a"
        assert cred["inline_auth"] is None

    def test_credential_name_only_is_requested(self):
        params = {"litellm_credential_name": "chatgpt_acct_a"}
        cred = resolve_chatgpt_deployment_credential(params)
        assert cred["requested"] is True
        assert cred["inline_auth"] is None

    def test_empty_params_not_requested(self):
        assert chatgpt_credential_requested({}) is False
        assert resolve_chatgpt_deployment_credential({})["requested"] is False
        assert chatgpt_credential_requested(None) is False


class TestAuthenticatorFromLitellmParams:
    def test_inline_valid_token(self):
        future = time.time() + 3600
        params = {
            "chatgpt_auth": {"access_token": "tok-a", "expires_at": future},
            "chatgpt_api_base": "https://acct-a.example.com",
        }
        auth = Authenticator.from_litellm_params(params)
        assert auth.get_access_token() == "tok-a"
        assert auth.get_api_base() == "https://acct-a.example.com"

    def test_inline_account_id_from_id_token(self):
        future = time.time() + 3600
        id_token = _make_jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-xyz"}}
        )
        params = {
            "chatgpt_auth": {
                "access_token": "tok-a",
                "expires_at": future,
                "id_token": id_token,
            }
        }
        auth = Authenticator.from_litellm_params(params)
        assert auth.get_account_id() == "acct-xyz"

    def test_requested_but_missing_credential_fails_no_fallback(self):
        """litellm_credential_name set but credential resolved empty -> explicit fail.

        Must NOT trigger interactive device login or read the global auth file.
        """
        params = {"litellm_credential_name": "chatgpt_acct_a"}
        auth = Authenticator.from_litellm_params(params)
        with (
            patch.object(auth, "_login_device_code") as mock_login,
            patch.object(auth, "_read_auth_file", wraps=auth._read_auth_file),
        ):
            with pytest.raises(GetAccessTokenError):
                auth.get_access_token()
            mock_login.assert_not_called()

    def test_inline_expired_token_with_refresh(self):
        past = time.time() - 10
        params = {
            "chatgpt_auth": {
                "access_token": "tok-old",
                "refresh_token": "ref-a",
                "expires_at": past,
            }
        }
        auth = Authenticator.from_litellm_params(params)
        refreshed = {
            "access_token": "tok-new",
            "refresh_token": "ref-a",
            "id_token": "id-a",
        }
        with patch.object(auth, "_refresh_tokens", return_value=refreshed):
            assert auth.get_access_token() == "tok-new"

    def test_inline_expired_token_refresh_failure_fails(self):
        from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

        past = time.time() - 10
        params = {
            "chatgpt_auth": {
                "access_token": "tok-old",
                "refresh_token": "ref-a",
                "expires_at": past,
            }
        }
        auth = Authenticator.from_litellm_params(params)
        with patch.object(
            auth,
            "_refresh_tokens",
            side_effect=RefreshAccessTokenError(message="boom", status_code=401),
        ):
            with pytest.raises(GetAccessTokenError):
                auth.get_access_token()

    def test_inline_refresh_not_persisted_to_global_file(self):
        """Inline credential refresh updates memory only; no global file write."""
        past = time.time() - 10
        params = {
            "chatgpt_auth": {
                "access_token": "tok-old",
                "refresh_token": "ref-a",
                "expires_at": past,
            }
        }
        auth = Authenticator.from_litellm_params(params)
        assert auth.auth_file is None  # inline mode keeps no file handle
        with (
            patch("builtins.open") as mock_open_call,
            patch.object(
                auth,
                "_refresh_tokens",
                return_value={"access_token": "tok-new", "refresh_token": "ref-a"},
            ),
        ):
            assert auth.get_access_token() == "tok-new"
            mock_open_call.assert_not_called()

    def test_not_requested_returns_global_authenticator(self):
        auth = Authenticator.from_litellm_params({})
        assert auth._inline_auth is None
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
