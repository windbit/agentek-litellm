"""
End-to-end tests for ChatGPT per-deployment credential selection via
litellm.completion(). ChatGPT chat completions are bridged to the Responses API,
so the deployment credential must be forwarded into litellm_params (which
get_litellm_params() otherwise drops) and reach validate_environment.

Source: litellm/main.py (forward_chatgpt_deployment_params injection)
"""

import json
import time
from unittest.mock import patch

import httpx
import pytest
import respx

import litellm
from litellm.exceptions import AuthenticationError
from litellm.types.utils import CredentialItem, ModelResponse


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    # Point the global ChatGPT auth dir at an empty temp dir so the global
    # fallback is never accidentally exercised by these per-deployment tests.
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    original = litellm.credential_list
    litellm.credential_list = []
    yield
    litellm.credential_list = original


def _register_chatgpt_credential(name: str, **values):
    litellm.credential_list = [
        CredentialItem(
            credential_name=name,
            credential_info={},
            credential_values=values,
        )
    ]


def test_completion_forwards_per_deployment_credential_to_responses():
    """The deployment credential must reach the bridged Responses API call."""
    future = time.time() + 3600
    _register_chatgpt_credential(
        "chatgpt_acct_a",
        chatgpt_auth={
            "access_token": "tok-a",
            "account_id": "acct-a",
            "expires_at": future,
        },
        chatgpt_api_base="https://acct-a.example.com",
    )

    captured = {}

    def _fake_responses(*args, **kwargs):
        captured.update(kwargs)
        return ModelResponse()

    with patch("litellm.responses", _fake_responses):
        litellm.completion(
            model="chatgpt/gpt-5.4",
            messages=[{"role": "user", "content": "hi"}],
            litellm_credential_name="chatgpt_acct_a",
        )

    # The bridge forwards litellm_params keys as kwargs into responses().
    assert captured.get("litellm_credential_name") == "chatgpt_acct_a"
    assert captured.get("chatgpt_auth", {}).get("access_token") == "tok-a"
    assert captured.get("chatgpt_api_base") == "https://acct-a.example.com"


@respx.mock
def test_completion_credential_authorization_reaches_wire():
    """Full path: completion -> Responses bridge -> HTTP POST carries the
    credential-derived Authorization / ChatGPT-Account-Id headers, and a
    user-supplied Authorization does not override them (req 6)."""
    future = time.time() + 3600
    _register_chatgpt_credential(
        "chatgpt_acct_a",
        chatgpt_auth={
            "access_token": "tok-a",
            "account_id": "acct-a",
            "expires_at": future,
        },
        chatgpt_api_base="https://acct-a.example.com",
    )

    sse_body = "\n".join(
        [
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "object": "response",
                        "created_at": 1700000000,
                        "status": "completed",
                        "model": "gpt-5.4",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "hello"}],
                            }
                        ],
                    },
                }
            ),
            "data: [DONE]",
            "",
        ]
    )
    route = respx.post("https://acct-a.example.com/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse_body
        )
    )

    litellm.completion(
        model="chatgpt/gpt-5.4",
        messages=[{"role": "user", "content": "hi"}],
        litellm_credential_name="chatgpt_acct_a",
        extra_headers={"Authorization": "Bearer attacker", "x-trace": "t1"},
    )

    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer tok-a"
    assert sent.headers["chatgpt-account-id"] == "acct-a"
    assert sent.headers["x-trace"] == "t1"


def test_completion_missing_credential_fails_closed():
    """A deployment that references a missing credential must fail explicitly,
    with no silent fallback to the global auth and no device login / network."""
    _register_chatgpt_credential("chatgpt_acct_a", api_key="unrelated")
    with pytest.raises(AuthenticationError):
        litellm.completion(
            model="chatgpt/gpt-5.4",
            messages=[{"role": "user", "content": "hi"}],
            litellm_credential_name="chatgpt_acct_missing",
        )
