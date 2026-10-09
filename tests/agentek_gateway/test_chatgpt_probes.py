import base64
import json
from typing import Mapping

import pytest

from agentek_gateway.subscriptions.events import LimitWindow
from agentek_gateway.subscriptions.model import Limits, Window
from agentek_gateway.subscriptions.providers.base import (
    AuthRejected,
    LimitReached,
    RefreshedTokens,
    RefreshRejected,
    Unclassified,
)
from agentek_gateway.subscriptions.providers.chatgpt import (
    RESPONSES_URL,
    USAGE_URL,
    ChatGPTProvider,
    ChatgptAuth,
    HttpReply,
)

from .test_chatgpt_provider import FIXTURES, NOW, fixture

AUTH = ChatgptAuth(access_token="at-1", refresh_token="rt-1", account_id="acct-1")


class ScriptedTransport:
    def __init__(self, reply: HttpReply) -> None:
        self.reply = reply
        self.posts: list[tuple[str, Mapping[str, str], Mapping[str, object]]] = []
        self.gets: list[tuple[str, Mapping[str, str]]] = []

    async def post_json(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, object]
    ) -> HttpReply:
        self.posts.append((url, headers, payload))
        return self.reply

    async def get(self, url: str, headers: Mapping[str, str]) -> HttpReply:
        self.gets.append((url, headers))
        return self.reply


def provider(
    status: int, body: str, headers: Mapping[str, str] | None = None
) -> tuple[ChatGPTProvider, ScriptedTransport]:
    transport = ScriptedTransport(HttpReply(status, headers or {}, body))
    return ChatGPTProvider(transport), transport


def sse(*events: Mapping[str, object]) -> str:
    return "".join(f"event: x\ndata: {json.dumps(event)}\n\n" for event in events)


async def test_healthy_probe_returns_the_limits_from_headers() -> None:
    body = sse(*fixture("success_stream_events.json")["events"])
    headers = fixture("success_headers_stream.json")["headers"]
    chatgpt, transport = provider(200, body, headers)

    result = await chatgpt.probe_health(AUTH, now=NOW)

    assert (result.ok, result.error, result.limits) == (
        True,
        None,
        Limits(None, Window(81.0, 1791948566.0)),
    )
    assert transport.posts[0][0] == RESPONSES_URL


async def test_probe_sends_the_credential_and_a_minimal_stateless_request() -> None:
    chatgpt, transport = provider(200, "")

    await chatgpt.probe_health(AUTH, now=NOW)

    _, headers, payload = transport.posts[0]
    assert (
        headers["Authorization"],
        headers["ChatGPT-Account-Id"],
        payload["store"],
        payload["stream"],
    ) == (
        "Bearer at-1",
        "acct-1",
        False,
        True,
    )


async def test_probe_that_gets_http_200_with_an_overload_event_fails() -> None:
    body = sse(
        {"type": "response.created"},
        fixture("stream_failure_server_is_overloaded.json")["event"],
    )
    chatgpt, _ = provider(200, body)

    result = await chatgpt.probe_health(AUTH, now=NOW)

    assert (result.ok, result.error) == (
        False,
        Unclassified(immediate=False, recognized=True),
    )


async def test_probe_that_hits_the_usage_limit_reports_the_window() -> None:
    data = fixture("error_429_usage_limit.json")
    chatgpt, _ = provider(429, data["body"], data["headers"])

    result = await chatgpt.probe_health(AUTH, now=NOW)

    assert (result.ok, result.error) == (
        False,
        LimitReached(LimitWindow.WEEKLY, 1791580236.0),
    )


async def test_probe_with_a_revoked_token_reports_authorization() -> None:
    data = fixture("error_401_token_revoked.json")
    chatgpt, _ = provider(401, data["body"])

    result = await chatgpt.probe_health(AUTH, now=NOW)

    assert result.error == AuthRejected()


async def test_usage_check_reads_both_windows() -> None:
    payload = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 40,
                "limit_window_seconds": 18000,
                "reset_at": 5000,
            },
            "secondary_window": {
                "used_percent": 70,
                "limit_window_seconds": 604800,
                "reset_after_seconds": 900,
            },
        }
    }
    chatgpt, transport = provider(200, json.dumps(payload))

    limits = await chatgpt.probe_usage(AUTH, now=NOW)

    assert (limits, transport.gets[0][0]) == (
        Limits(Window(40, 5000), Window(70, NOW + 900)),
        USAGE_URL,
    )


@pytest.mark.parametrize(
    ("status", "body"), [(500, "oops"), (200, "not json"), (200, "{}")]
)
async def test_usage_check_without_a_usable_answer_returns_nothing(
    status: int, body: str
) -> None:
    chatgpt, _ = provider(status, body)

    assert await chatgpt.probe_usage(AUTH, now=NOW) is None


def jwt(expires_at: int) -> str:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": expires_at}).encode())
        .decode()
        .rstrip("=")
    )
    return f"h.{payload}.s"


async def test_refresh_returns_the_rotated_pair_and_expiry_from_the_response() -> None:
    body = json.dumps(
        {
            "access_token": "at-2",
            "refresh_token": "rt-2",
            "id_token": "id-2",
            "expires_in": 3600,
        }
    )
    chatgpt, transport = provider(200, body)

    outcome = await chatgpt.refresh("rt-1", now=NOW)

    assert outcome == RefreshedTokens("at-2", "rt-2", "id-2", NOW + 3600)
    assert transport.posts[0][2]["refresh_token"] == "rt-1"


async def test_refresh_falls_back_to_the_token_expiry_and_keeps_the_old_refresh_token() -> (
    None
):
    body = json.dumps({"access_token": jwt(9_999_999_999)})
    chatgpt, _ = provider(200, body)

    outcome = await chatgpt.refresh("rt-1", now=NOW)

    assert outcome == RefreshedTokens(jwt(9_999_999_999), "rt-1", None, 9_999_999_999.0)


@pytest.mark.parametrize(
    "code", ["invalid_grant", "refresh_token_reused", "refresh_token_expired"]
)
async def test_refresh_rejected_for_good_when_the_grant_is_gone(code: str) -> None:
    chatgpt, _ = provider(400, json.dumps({"error": code}))

    assert await chatgpt.refresh("rt-1", now=NOW) == RefreshRejected(permanent=True)


async def test_refresh_rejection_with_a_nested_error_object_is_understood() -> None:
    chatgpt, _ = provider(401, json.dumps({"error": {"code": "refresh_token_reused"}}))

    assert await chatgpt.refresh("rt-1", now=NOW) == RefreshRejected(permanent=True)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, "oops"),
        (503, "{}"),
        (400, json.dumps({"error": "temporarily_unavailable"})),
    ],
)
async def test_refresh_failure_that_may_pass_is_not_permanent(
    status: int, body: str
) -> None:
    chatgpt, _ = provider(status, body)

    assert await chatgpt.refresh("rt-1", now=NOW) == RefreshRejected(permanent=False)


def test_fixture_directory_has_no_stray_secrets() -> None:
    text = "".join(path.read_text() for path in FIXTURES.iterdir())

    assert "eyJ" not in text and "Bearer " not in text
