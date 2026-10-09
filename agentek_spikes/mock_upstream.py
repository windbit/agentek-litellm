"""Mock ChatGPT Codex backend (POST /responses) for the subscription-pool spikes.

Behaviour is chosen per account (ChatGPT-Account-Id header) from a script queue set via
POST /_ctl/script {"account": "acctA", "script": ["usage_limit", "ok"], "default": "ok"}.
Every inbound request is appended to $MOCK_LOG as one JSON line.
Shapes of 429/headers follow sub2api (ratelimit_service.go) and llmSubscriptions.ts fixtures.
"""
import asyncio
import json
import os
import time
import uuid

from aiohttp import web

LOG_PATH = os.environ.get("MOCK_LOG", "/spikes/logs/mock_upstream.jsonl")
SCRIPTS: dict = {}
DEFAULTS: dict = {}
DELAY_S: dict = {}


def codex_headers(used_primary=12, used_secondary=31, reset_in=3600):
    now = int(time.time())
    return {
        "x-codex-primary-used-percent": str(used_primary),
        "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-after-seconds": str(reset_in),
        "x-codex-primary-reset-at": str(now + reset_in),
        "x-codex-secondary-used-percent": str(used_secondary),
        "x-codex-secondary-window-minutes": "10080",
        "x-codex-secondary-reset-after-seconds": str(reset_in * 100),
        "x-codex-secondary-reset-at": str(now + reset_in * 100),
        "x-codex-primary-over-secondary-limit-percent": "0",
    }


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def resp_obj(model, status, output=None, usage=None, error=None):
    obj = {
        "id": "resp_" + uuid.uuid4().hex[:24],
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output or [],
    }
    if usage:
        obj["usage"] = usage
    if error:
        obj["error"] = error
    return obj


async def handle_responses(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    account = request.headers.get("ChatGPT-Account-Id", "?")
    queue = SCRIPTS.setdefault(account, [])
    action = queue.pop(0) if queue else DEFAULTS.get(account, "ok")
    model = body.get("model", "gpt-5.4")
    entry = {
        "ts": time.time(),
        "account": account,
        "action": action,
        "model": model,
        "session_id": request.headers.get("session_id"),
        "prompt_cache_key": body.get("prompt_cache_key"),
        "body_keys": sorted(body.keys()),
        "headers": {k: v for k, v in request.headers.items() if k.lower() not in ("authorization",)},
    }
    with open(LOG_PATH, "a") as handle:
        handle.write(json.dumps(entry) + "\n")

    delay = DELAY_S.get(account, 0)
    if delay:
        await asyncio.sleep(delay)

    if action == "usage_limit":
        reset_in = 7200
        payload = {
            "error": {
                "type": "usage_limit_reached",
                "message": "The usage limit has been reached",
                "plan_type": "plus",
                "resets_at": int(time.time()) + reset_in,
                "resets_in_seconds": reset_in,
            }
        }
        return web.json_response(
            payload, status=429, headers=codex_headers(100, 100, reset_in)
        )
    if action == "rate_limit_plain":
        return web.json_response(
            {"error": {"type": "rate_limit_exceeded", "message": "Rate limit reached"}}, status=429
        )
    if action == "model_not_supported":
        return web.json_response(
            {"detail": f"The '{model}' model is not supported when using Codex with a ChatGPT account."},
            status=400,
        )
    if action == "unauthorized":
        return web.json_response({"detail": "Unauthorized"}, status=401)
    if action == "overloaded":
        return web.json_response({"error": {"type": "server_error", "message": "overloaded"}}, status=503)
    if action == "internal":
        return web.json_response({"error": {"type": "server_error", "message": "boom"}}, status=500)

    if action == "close_after_headers":
        early = web.StreamResponse(status=200, headers={"content-type": "text/event-stream", **codex_headers()})
        await early.prepare(request)
        request.transport.abort()
        return early

    stream = web.StreamResponse(
        status=200,
        headers={"content-type": "text/event-stream", "cache-control": "no-cache", **codex_headers()},
    )
    await stream.prepare(request)
    await stream.write(sse("response.created", {"type": "response.created", "response": resp_obj(model, "in_progress")}))

    if action == "sse_failed":
        await stream.write(
            sse(
                "response.failed",
                {
                    "type": "response.failed",
                    "response": resp_obj(
                        model,
                        "failed",
                        error={"code": "usage_limit_reached", "message": "The usage limit has been reached"},
                    ),
                },
            )
        )
        await stream.write_eof()
        return stream

    chunks = ["Hel", "lo ", "from ", "mock ", account]
    if action == "slow_stream":
        chunks = [f"c{i} " for i in range(10)]
    for idx, text in enumerate(chunks):
        if action == "midstream_error" and idx == 2:
            await stream.write(
                sse(
                    "error",
                    {"type": "error", "code": "rate_limit_exceeded", "message": "Rate limit reached mid stream"},
                )
            )
            await stream.write_eof()
            return stream
        if action == "midstream_abort" and idx == 2:
            request.transport.abort()
            return stream
        await stream.write(
            sse(
                "response.output_text.delta",
                {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": text},
            )
        )
        await asyncio.sleep(0.6 if action == "slow_stream" else 0.05)

    full = "".join(chunks)
    item = {
        "id": "msg_1",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": full}],
    }
    await stream.write(sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}))
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
    await stream.write(
        sse("response.completed", {"type": "response.completed", "response": resp_obj(model, "completed", [item], usage)})
    )
    await stream.write_eof()
    return stream


async def handle_ctl(request: web.Request) -> web.Response:
    data = await request.json()
    account = data["account"]
    if "script" in data:
        SCRIPTS[account] = list(data["script"])
    if "default" in data:
        DEFAULTS[account] = data["default"]
    if "delay" in data:
        DELAY_S[account] = data["delay"]
    return web.json_response({"ok": True, "scripts": SCRIPTS, "defaults": DEFAULTS})


async def handle_reset(request: web.Request) -> web.Response:
    SCRIPTS.clear()
    DEFAULTS.clear()
    DELAY_S.clear()
    open(LOG_PATH, "w").close()
    return web.json_response({"ok": True})


app = web.Application()
app.router.add_post("/responses", handle_responses)
app.router.add_post("/_ctl/script", handle_ctl)
app.router.add_post("/_ctl/reset", handle_reset)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=9000, print=None)
