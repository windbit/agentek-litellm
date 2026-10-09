"""In-process stand-in for the ChatGPT Codex backend; behaviour is scripted per ChatGPT-Account-Id."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

from aiohttp import web

WINDOW_5H_MINUTES = 300
WINDOW_WEEK_MINUTES = 10080
SSE_CHUNKS = ("Hel", "lo ", "from ", "mock")
SECONDS_PER_MINUTE = 60


def codex_headers(
    used_primary: int = 12, used_secondary: int = 31, reset_in: int = 3600
) -> dict[str, str]:
    now = int(time.time())
    return {
        "x-codex-primary-used-percent": str(used_primary),
        "x-codex-primary-window-minutes": str(WINDOW_5H_MINUTES),
        "x-codex-primary-reset-at": str(now + reset_in),
        "x-codex-secondary-used-percent": str(used_secondary),
        "x-codex-secondary-window-minutes": str(WINDOW_WEEK_MINUTES),
        "x-codex-secondary-reset-at": str(now + reset_in * 100),
    }


def sse(event: str, data: dict[str, object]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def response_object(
    model: str, status: str, output: list[object] | None = None, error: object = None
) -> dict[str, object]:
    obj: dict[str, object] = {
        "id": "resp_" + uuid.uuid4().hex[:24],
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output or [],
    }
    if error:
        obj["error"] = error
    return obj


@dataclass
class Received:
    account: str
    action: str
    model: str
    session_id: str | None
    body: dict[str, object]


@dataclass
class MockCodex:
    scripts: dict[str, list[str]] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    delays: dict[str, float] = field(default_factory=dict)
    received: list[Received] = field(default_factory=list)
    reset_in_s: int = 7200
    _runner: web.AppRunner | None = None
    base_url: str = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/responses", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    def script(self, account: str, *actions: str, default: str | None = None) -> None:
        self.scripts[account] = list(actions)
        if default:
            self.defaults[account] = default

    def accounts_served(self) -> list[str]:
        return [item.account for item in self.received]

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        account = request.headers.get("ChatGPT-Account-Id", "?")
        queue = self.scripts.setdefault(account, [])
        action = queue.pop(0) if queue else self.defaults.get(account, "ok")
        model = str(body.get("model", "gpt-5.4"))
        self.received.append(
            Received(account, action, model, request.headers.get("session_id"), body)
        )
        if self.delays.get(account):
            await asyncio.sleep(self.delays[account])
        failure = self._failure_reply(action, model)
        if failure is not None:
            return failure
        return await self._stream_reply(request, action, account, model)

    @staticmethod
    def _limit_headers(action: str) -> dict[str, str]:
        match action:
            case "ok_high":
                return codex_headers(used_primary=96, used_secondary=10)
            case "ok_nolimits":
                return {}
        return codex_headers()

    def _failure_reply(self, action: str, model: str) -> web.Response | None:
        match action:
            case "usage_limit":
                payload = {
                    "error": {
                        "type": "usage_limit_reached",
                        "message": "The usage limit has been reached",
                        "plan_type": "plus",
                        "resets_at": int(time.time()) + self.reset_in_s,
                        "resets_in_seconds": self.reset_in_s,
                    }
                }
                return web.json_response(
                    payload,
                    status=429,
                    headers=codex_headers(100, 100, self.reset_in_s),
                )
            case "model_not_supported":
                detail = f"The '{model}' model is not supported when using Codex with a ChatGPT account."
                return web.json_response({"detail": detail}, status=400)
            case "deactivated":
                payload = {
                    "error": {
                        "code": "account_deactivated",
                        "message": "Your account has been deactivated.",
                    }
                }
                return web.json_response(payload, status=401)
            case "unauthorized":
                return web.json_response({"detail": "Unauthorized"}, status=401)
            case "overloaded":
                return web.json_response(
                    {"error": {"type": "server_error", "message": "overloaded"}},
                    status=503,
                )
            case "rate_limit_plain":
                return web.json_response(
                    {"error": {"type": "rate_limit_exceeded", "message": "slow"}},
                    status=429,
                )
        return None

    async def _stream_reply(
        self, request: web.Request, action: str, account: str, model: str
    ) -> web.StreamResponse:
        stream = web.StreamResponse(
            status=200,
            headers={
                "content-type": "text/event-stream",
                **self._limit_headers(action),
            },
        )
        await stream.prepare(request)
        created = {
            "type": "response.created",
            "response": response_object(model, "in_progress"),
        }
        await stream.write(sse("response.created", created))
        if action == "sse_failed":
            error = {"code": "usage_limit_reached", "message": "limit"}
            failed = {
                "type": "response.failed",
                "response": response_object(model, "failed", error=error),
            }
            await stream.write(sse("response.failed", failed))
            await stream.write_eof()
            return stream
        for index, text in enumerate(SSE_CHUNKS):
            if action == "midstream_abort" and index == 2:
                request.transport.abort()  # type: ignore[union-attr]
                return stream
            delta = {
                "type": "response.output_text.delta",
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "delta": text,
            }
            await stream.write(sse("response.output_text.delta", delta))
            await asyncio.sleep(0.4 if action == "slow_stream" else 0.01)
        item = {
            "id": "msg_1",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "".join(SSE_CHUNKS)}],
        }
        await stream.write(
            sse(
                "response.output_item.done",
                {"type": "response.output_item.done", "output_index": 0, "item": item},
            )
        )
        completed = {
            "type": "response.completed",
            "response": response_object(model, "completed", [item]),
        }
        await stream.write(sse("response.completed", completed))
        await stream.write_eof()
        return stream
