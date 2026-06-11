"""
Tests for ChatGPT subscription Chat Completions transformation.

Source: litellm/llms/chatgpt/chat/transformation.py
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from litellm.exceptions import AuthenticationError
from litellm.llms.chatgpt.chat.transformation import ChatGPTConfig


class TestChatGPTChatValidateEnvironment:
    def test_per_deployment_credential_headers(self):
        future = time.time() + 3600
        config = ChatGPTConfig()
        litellm_params = {
            "litellm_credential_name": "chatgpt_acct_a",
            "chatgpt_auth": {
                "access_token": "tok-a",
                "account_id": "acct-a",
                "expires_at": future,
            },
        }
        headers = config.validate_environment(
            headers={
                "originator": "custom-origin",
                "authorization": "Bearer attacker",
                "CHATGPT-ACCOUNT-ID": "acct-attacker",
            },
            model="gpt-5.4",
            messages=[{"role": "user", "content": "hi"}],
            optional_params={},
            litellm_params=litellm_params,
        )
        # Credential-derived auth wins over user-supplied headers (req 6).
        assert headers["Authorization"] == "Bearer tok-a"
        assert headers["ChatGPT-Account-Id"] == "acct-a"
        assert "authorization" not in headers
        assert "CHATGPT-ACCOUNT-ID" not in headers
        # Non-auth user headers preserved.
        assert headers["originator"] == "custom-origin"

    def test_missing_credential_raises_auth_error(self):
        config = ChatGPTConfig()
        litellm_params = {"litellm_credential_name": "chatgpt_acct_missing"}
        with pytest.raises(AuthenticationError):
            config.validate_environment(
                headers={},
                model="gpt-5.4",
                messages=[{"role": "user", "content": "hi"}],
                optional_params={},
                litellm_params=litellm_params,
            )

    def test_provider_info_defers_token_resolution(self):
        """Provider discovery must not resolve a token (avoids network refresh /
        interactive device-login). The token is injected on the request hot path.
        """
        config = ChatGPTConfig()
        with (
            patch.object(config.authenticator, "get_access_token") as mock_token,
            patch.object(
                config.authenticator, "get_api_base", return_value="https://base"
            ),
        ):
            api_base, api_key, provider = config._get_openai_compatible_provider_info(
                model="gpt-5.4",
                api_base=None,
                api_key="passthrough-key",
                custom_llm_provider="chatgpt",
            )
            mock_token.assert_not_called()
            assert api_base == "https://base"
            assert api_key == "passthrough-key"
            assert provider == "chatgpt"
