"""The probe transport against a real local HTTP server: wire format, status mapping, proxy env, transport failure."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
from aiohttp import web

from agentek_gateway.subscriptions.events import LimitWindow
from agentek_gateway.subscriptions.providers.base import AuthRejected, LimitReached
from agentek_gateway.subscriptions.providers.chatgpt import (
    RESPONSES_URL,
    ChatgptAuth,
    ChatGPTProvider,
    HttpReply,
)
from agentek_gateway.subscriptions.providers.transport import HttpxProbeTransport

from .test_chatgpt_provider import NOW, fixture


class LocalProviderTransport(HttpxProbeTransport):
    """The real transport with the provider's responses URL pointed at a local server."""

    def __init__(self, base: str) -> None:
        super().__init__()
        self._base = base

    async def post_json(self, url, headers, payload) -> HttpReply:  # type: ignore[no-untyped-def]
        local = url.replace(RESPONSES_URL, f"{self._base}/responses")
        return await super().post_json(local, headers, payload)


AUTH = ChatgptAuth(access_token="at-1", refresh_token="rt-1", account_id="acct-1")


@dataclass
class Seen:
    method: str
    path_qs: str
    headers: dict[str, str]
    body: str


@dataclass
class Server:
    base: str
    seen: list[Seen] = field(default_factory=list)


@asynccontextmanager
async def serving(
    status: int = 200,
    body: str = "{}",
    headers: dict[str, str] | None = None,
) -> AsyncIterator[Server]:
    server = Server("")

    async def handle(request: web.Request) -> web.Response:
        server.seen.append(
            Seen(
                request.method,
                request.path_qs,
                dict(request.headers),
                await request.text(),
            )
        )
        return web.Response(
            status=status, text=body, headers={"X-Test-Header": "7", **(headers or {})}
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # noqa: SLF001
    server.base = f"http://127.0.0.1:{port}"
    try:
        yield server
    finally:
        await runner.cleanup()


async def test_post_sends_json_and_headers_and_returns_status_headers_and_body() -> (
    None
):
    async with serving(429, '{"error": "limit"}') as server:
        reply = await HttpxProbeTransport().post_json(
            f"{server.base}/responses", {"Authorization": "Bearer t"}, {"model": "m"}
        )

    sent = server.seen[0]
    assert (
        sent.method,
        sent.headers["Authorization"],
        json.loads(sent.body),
        reply.status,
        reply.body,
        reply.headers["x-test-header"],
    ) == ("POST", "Bearer t", {"model": "m"}, 429, '{"error": "limit"}', "7")


async def test_get_sends_headers_and_returns_the_reply() -> None:
    async with serving(200, '{"ok": 1}') as server:
        reply = await HttpxProbeTransport().get(
            f"{server.base}/usage?x=1", {"chatgpt-account-id": "acct"}
        )

    sent = server.seen[0]
    assert (
        sent.method,
        sent.path_qs,
        sent.headers["chatgpt-account-id"],
        reply.status,
    ) == (
        "GET",
        "/usage?x=1",
        "acct",
        200,
    )


async def test_requests_go_through_the_proxy_named_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with serving(200, "via proxy") as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.base)
        monkeypatch.setenv("http_proxy", proxy.base)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        reply = await HttpxProbeTransport().get("http://upstream.invalid/usage", {})

    assert (reply.body, proxy.seen[0].headers["Host"]) == (
        "via proxy",
        "upstream.invalid",
    )


async def test_a_dead_endpoint_raises_instead_of_returning_a_reply() -> None:
    async with serving() as server:
        dead = server.base

    with pytest.raises(httpx.TransportError):
        await HttpxProbeTransport(timeout_s=2).post_json(f"{dead}/x", {}, {})


async def test_provider_classifies_a_recorded_usage_limit_reply_served_over_real_http() -> (
    None
):
    recorded = fixture("error_429_usage_limit.json")
    async with serving(429, recorded["body"], recorded["headers"]) as server:
        provider = ChatGPTProvider(LocalProviderTransport(server.base), "probe-model")

        result = await provider.probe_health(AUTH, now=NOW)

    assert (result.ok, result.error, server.seen[0].headers["ChatGPT-Account-Id"]) == (
        False,
        LimitReached(LimitWindow.WEEKLY, 1791580236.0),
        "acct-1",
    )


async def test_provider_reads_a_revoked_token_reply_served_over_real_http() -> None:
    recorded = fixture("error_401_token_revoked.json")
    async with serving(401, recorded["body"]) as server:
        provider = ChatGPTProvider(LocalProviderTransport(server.base), "probe-model")

        result = await provider.probe_health(AUTH, now=NOW)

    assert result.error == AuthRejected()
