"""
End-to-end tests for ChatGPT per-deployment credential selection via
litellm.completion(). ChatGPT chat completions are bridged to the Responses API,
so the deployment credential must be forwarded into litellm_params (which
get_litellm_params() otherwise drops) and reach validate_environment.

Source: litellm/main.py (forward_chatgpt_deployment_params injection)
"""

import base64
import json
import time
from unittest.mock import patch

import httpx
import pytest
import respx

import litellm
from litellm.exceptions import AuthenticationError
from litellm.types.utils import CredentialItem, ModelResponse


def _b64url(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()


def _make_chatgpt_jwt(account_id: str, exp: float) -> str:
    """Minimal unsigned JWT carrying the ChatGPT account id and an expiry, as
    parsed by Authenticator._decode_jwt_claims."""
    header = _b64url({"alg": "none", "typ": "JWT"})
    payload = _b64url(
        {
            "exp": int(exp),
            "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
        }
    )
    return f"{header}.{payload}.sig"


_SSE_BODY = "\n".join(
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


def _write_auth_file(tmp_path, name: str, auth: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(auth))
    return str(path)


def test_completion_forwards_per_deployment_credential_to_responses(tmp_path):
    """The deployment credential must reach the bridged Responses API call."""
    future = time.time() + 3600
    auth_file = _write_auth_file(
        tmp_path,
        "acct_a.json",
        {"access_token": "tok-a", "account_id": "acct-a", "expires_at": future},
    )
    _register_chatgpt_credential(
        "chatgpt_acct_a",
        chatgpt_auth_file=auth_file,
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
    assert captured.get("chatgpt_auth_file") == auth_file
    assert captured.get("chatgpt_api_base") == "https://acct-a.example.com"


@respx.mock
def test_completion_credential_authorization_reaches_wire(tmp_path):
    """Full path: completion -> Responses bridge -> HTTP POST carries the
    credential-derived Authorization / ChatGPT-Account-Id headers, and a
    user-supplied Authorization does not override them."""
    future = time.time() + 3600
    auth_file = _write_auth_file(
        tmp_path,
        "acct_a.json",
        {"access_token": "tok-a", "account_id": "acct-a", "expires_at": future},
    )
    _register_chatgpt_credential(
        "chatgpt_acct_a",
        chatgpt_auth_file=auth_file,
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
        extra_headers={
            "authorization": "Bearer attacker",
            "CHATGPT-ACCOUNT-ID": "acct-attacker",
            "x-trace": "t1",
        },
    )

    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer tok-a"
    assert sent.headers["chatgpt-account-id"] == "acct-a"
    assert sent.headers["x-trace"] == "t1"


@respx.mock
def test_completion_expired_credential_refresh_reused_no_attacker_headers(tmp_path):
    """A per-deployment credential that is expired-on-arrival is refreshed ONCE
    during validate_environment. The post-merge header reassertion must REUSE that
    refreshed token (not refresh a second time, which would fail for rotating
    refresh tokens) and must drop the user-supplied case-variant Authorization /
    ChatGPT-Account-Id so the attacker values never reach the wire."""
    past = time.time() - 3600
    future = time.time() + 3600
    auth_file = _write_auth_file(
        tmp_path,
        "acct_a.json",
        {
            "access_token": "expired-a",
            "refresh_token": "rt-a",
            "id_token": "idt-a",
            "account_id": "acct-a",
            "expires_at": past,
        },
    )
    _register_chatgpt_credential(
        "chatgpt_acct_a",
        chatgpt_auth_file=auth_file,
        chatgpt_api_base="https://acct-a.example.com",
    )

    refreshed_access_token = _make_chatgpt_jwt(account_id="acct-a", exp=future)
    refresh_route = respx.post("https://auth.openai.com/oauth/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": refreshed_access_token,
                "id_token": refreshed_access_token,
                # Rotated refresh token: a second refresh with the original
                # "rt-a" would fail upstream, which is exactly why the reassert
                # must reuse the already-refreshed authenticator.
                "refresh_token": "rt-a-rotated",
            },
        )
    )
    responses_route = respx.post("https://acct-a.example.com/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=_SSE_BODY
        )
    )

    litellm.completion(
        model="chatgpt/gpt-5.4",
        messages=[{"role": "user", "content": "hi"}],
        litellm_credential_name="chatgpt_acct_a",
        extra_headers={
            "authorization": "Bearer attacker",
            "CHATGPT-ACCOUNT-ID": "acct-attacker",
            "x-trace": "t1",
        },
    )

    # Refreshed exactly once and reused; never a second refresh.
    assert refresh_route.call_count == 1
    assert responses_route.called
    sent = responses_route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {refreshed_access_token}"
    assert sent.headers["chatgpt-account-id"] == "acct-a"
    assert sent.headers["x-trace"] == "t1"


@respx.mock
def test_completion_reassert_refresh_failure_fails_closed(tmp_path):
    """If the credential cannot be re-resolved at reassert time (e.g. the cached
    authenticator is unavailable and a fresh refresh fails), the request must
    fail closed; never sent with the attacker's stripped/forged headers."""
    past = time.time() - 3600
    auth_file = _write_auth_file(
        tmp_path,
        "acct_a.json",
        {
            "access_token": "expired-a",
            "refresh_token": "rt-a",
            "id_token": "idt-a",
            "account_id": "acct-a",
            "expires_at": past,
        },
    )
    _register_chatgpt_credential(
        "chatgpt_acct_a",
        chatgpt_auth_file=auth_file,
        chatgpt_api_base="https://acct-a.example.com",
    )

    # Every refresh attempt fails -> validate_environment itself fails closed.
    refresh_route = respx.post("https://auth.openai.com/oauth/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    responses_route = respx.post("https://acct-a.example.com/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=_SSE_BODY
        )
    )

    with pytest.raises(AuthenticationError):
        litellm.completion(
            model="chatgpt/gpt-5.4",
            messages=[{"role": "user", "content": "hi"}],
            litellm_credential_name="chatgpt_acct_a",
            extra_headers={
                "authorization": "Bearer attacker",
                "CHATGPT-ACCOUNT-ID": "acct-attacker",
            },
        )

    assert refresh_route.called
    # The upstream Responses endpoint must never be reached.
    assert not responses_route.called


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
