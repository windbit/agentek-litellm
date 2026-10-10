"""Scripted ChatGPT Codex backend with a control endpoint, for gateway load tests.

Serves plain HTTP on 8000 and TLS on 443 (chatgpt.com and auth.openai.com resolve to it inside the test network).
Needs the plugin tests mounted at /tests for the scripted backend behind /responses.
"""

import asyncio
import base64
import json
import ssl
import sys
import time

from aiohttp import web

sys.path.insert(0, "/tests")
from mock_codex import MockCodex  # noqa: E402

mock = MockCodex()
log: list[list[object]] = []
oauth = {"delay": 0.0, "count": 0}
TOKEN_LIFETIME_S = 7200


def jwt(claims: dict[str, object]) -> str:
    def encode(part: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode(claims)}.sig"


async def responses(request: web.Request) -> web.StreamResponse:
    reply = await mock._handle(request)
    last = mock.received[-1]
    log.append([time.time(), last.account, last.action])
    return reply


async def control(request: web.Request) -> web.Response:
    body = await request.json()
    if body.get("reset"):
        mock.scripts.clear()
        mock.defaults.clear()
        mock.delays.clear()
        mock.received.clear()
        log.clear()
        oauth.update(delay=0.0, count=0)
    mock.defaults.update(body.get("defaults") or {})
    mock.delays.update(body.get("delays") or {})
    if "oauth_delay" in body:
        oauth["delay"] = body["oauth_delay"]
    return web.json_response({"ok": True})


async def stats(request: web.Request) -> web.Response:
    since = float(request.query.get("since", 0))
    counts: dict[str, dict[str, int]] = {}
    for at, account, action in log:
        if at >= since:  # type: ignore[operator]
            per_account = counts.setdefault(str(account), {})
            per_account[str(action)] = per_account.get(str(action), 0) + 1
    return web.json_response(counts)


async def get_log(request: web.Request) -> web.Response:
    return web.json_response(log)


async def token(request: web.Request) -> web.Response:
    oauth["count"] += 1
    await asyncio.sleep(oauth["delay"])
    body = await request.json()
    account = str(body.get("refresh_token", "x|y")).split("|")[0]
    claims = {
        "exp": int(time.time()) + TOKEN_LIFETIME_S,
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
    }
    return web.json_response(
        {
            "access_token": jwt(claims),
            "refresh_token": f"{account}|rt{oauth['count']}",
            "id_token": jwt(claims),
            "expires_in": TOKEN_LIFETIME_S,
        }
    )


async def usage(request: web.Request) -> web.Response:
    now = int(time.time())
    window = {"used_percent": 10, "limit_window_seconds": 18000, "reset_at": now + 3600}
    return web.json_response(
        {
            "plan_type": "plus",
            "rate_limit": {"primary_window": window, "secondary_window": window},
        }
    )


async def analyze(request: web.Request) -> web.Response:
    return web.json_response([])


async def anonymize(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response({"text": body.get("text", ""), "items": []})


def build_app() -> web.Application:
    app = web.Application()
    for path in ("/responses", "/backend-api/codex/responses"):
        app.router.add_post(path, responses)
    app.router.add_post("/ctl", control)
    app.router.add_get("/stats", stats)
    app.router.add_get("/log", get_log)
    app.router.add_post("/oauth/token", token)
    app.router.add_get("/backend-api/wham/usage", usage)
    app.router.add_post("/analyze", analyze)
    app.router.add_post("/anonymize", anonymize)
    return app


async def main() -> None:
    runner = web.AppRunner(build_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    await web.TCPSite(runner, "0.0.0.0", 3000).start()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain("/certs/server.crt", "/certs/server.key")
    await web.TCPSite(runner, "0.0.0.0", 443, ssl_context=tls).start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
