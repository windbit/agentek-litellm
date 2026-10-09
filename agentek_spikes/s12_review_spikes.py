"""Review follow-up spikes on a real Router (in-process, mock ChatGPT backend on a local port, no gateway image needed).

  R1/R2  400 "model not supported" remapped to a retryable status: single group, 3 all bad, 3 with one bad
  R3     own "no available subscriptions" error: filter calls with and without exc.num_retries = 0
  R4     every deployment of the group in router cooldown: what does the client get, does cooldown_time=0 help

usage (inside the spike image, from services/litellm):
  docker run --rm -v $PWD/agentek_spikes:/spikes --entrypoint python <image> /spikes/s12_review_spikes.py [r1|r3|r4]
"""
import asyncio
import contextvars
import json
import os
import sys
import time

import litellm
from aiohttp import web
from litellm import Router
from litellm.integrations.custom_logger import CustomLogger

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["MOCK_LOG"] = "/tmp/mock_review.jsonl"
sys.path.insert(0, HERE)
import mock_upstream  # noqa: E402

ATT = contextvars.ContextVar("att", default=None)
FAILURE_STATUSES = []
VARIANT = {}
FILTER_CALLS = []


class NoSubscriptions(litellm.NotFoundError):
    """Own error that the router's retry loop treats as final (NotFoundError) but the proxy reports as 429."""

    def __init__(self, model):
        super().__init__(message="agentek: no available subscriptions", model=model, llm_provider="agentek")
        self.status_code = 429
        self.headers = {"retry-after": "10"}


class Plug(CustomLogger):
    """Fixed-order filter with a per-request attempted set; sets how many untried candidates remain."""

    attempted = {}

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):
        FILTER_CALLS.append(len(healthy_deployments))
        key = (request_kwargs or {}).get("litellm_call_id")
        tried = self.attempted.setdefault(key, set())
        cands = sorted((d for d in healthy_deployments if d["model_info"]["id"] not in tried), key=lambda d: d["model_info"]["id"])
        if not cands:
            if VARIANT.get("own_error_class") == "final":
                raise NoSubscriptions(model)
            exc = litellm.RateLimitError(message="agentek: no available subscriptions", llm_provider="agentek", model=model)
            exc.headers = {"retry-after": "10"}
            if VARIANT.get("own_error_no_retries"):
                exc.num_retries = 0
            raise exc
        tried.add(cands[0]["model_info"]["id"])
        ATT.set({"others": len(cands) - 1})
        return [cands[0]]

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        exc = kwargs.get("exception")
        FAILURE_STATUSES.append(getattr(exc, "status_code", None))


def patch_provider(variant):
    from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

    if not hasattr(ChatGPTResponsesAPIConfig, "_orig_gec"):
        ChatGPTResponsesAPIConfig._orig_gec = ChatGPTResponsesAPIConfig.get_error_class

    def wrapped(self, error_message, status_code, headers):
        att = ATT.get() or {"others": 0}
        status, last = status_code, att["others"] == 0
        if status_code == 400 and "is not supported when using Codex" in str(error_message) and variant["status"]:
            if not (variant["cond"] and last):
                status = variant["status"]
            elif variant["cond"] and variant.get("keep_400_on_last", True):
                status = 400
            else:
                status = variant["status"]
        exc = ChatGPTResponsesAPIConfig._orig_gec(self, error_message, status, headers)
        if variant.get("zero_retries_on_last") and last and status != 400:
            exc.num_retries = 0
        return exc

    ChatGPTResponsesAPIConfig.get_error_class = wrapped


def deployments(n, cooldown_time=None):
    out = []
    for i in range(n):
        params = {"model": "chatgpt/gpt-5.4", "chatgpt_api_base": f"http://127.0.0.1:{PORT}",
                  "chatgpt_auth": {"access_token": "x", "expires_at": 4102444800, "account_id": f"acct{'ABC'[i]}"}}
        if cooldown_time is not None:
            params["cooldown_time"] = cooldown_time
        out.append({"model_name": "m", "litellm_params": params, "model_info": {"id": f"d{i}", "mode": "responses"}})
    return out


def attempts():
    return [json.loads(line)["account"][-1] + ":" + json.loads(line)["action"][:3] for line in open(mock_upstream.LOG_PATH) if line.strip()]


async def one(router, chat=False):
    import uuid

    t0 = time.time()
    try:
        if chat:
            await router.acompletion(model="m", messages=[{"role": "user", "content": "hi"}], litellm_call_id=str(uuid.uuid4()))
        else:
            await router.aresponses(model="m", input="hi", stream=False, litellm_call_id=str(uuid.uuid4()))
        out = "ok"
    except Exception as err:
        out = f"{type(err).__name__} {getattr(err, 'status_code', None)} retry-after={getattr(err, 'headers', None) and err.headers.get('retry-after')}"
    return out, round(time.time() - t0, 1)


async def reset(scripts):
    mock_upstream.SCRIPTS.clear(); mock_upstream.DEFAULTS.clear()
    open(mock_upstream.LOG_PATH, "w").close()
    for acct, action in scripts.items():
        mock_upstream.DEFAULTS[acct] = action
    Plug.attempted.clear(); FILTER_CALLS.clear(); FAILURE_STATUSES.clear()


async def r1():
    VARIANT["own_error_no_retries"] = os.environ.get("OWN_NO_RETRIES") == "1"
    variants = {
        "V0 always 403": {"status": 403, "cond": False},
        "V1 403, num_retries=0 on last candidate": {"status": 403, "cond": False, "zero_retries_on_last": True},
        "V2 409, num_retries=0 on last candidate": {"status": 409, "cond": False, "zero_retries_on_last": True},
        "V3 409 always": {"status": 409, "cond": False},
        "V4 409 only while untried candidates remain, else keep 400": {"status": 409, "cond": True},
    }
    scen = [("group of 1, bad", 1, {"acctA": "model_not_supported"}),
            ("group of 3, all bad", 3, {a: "model_not_supported" for a in ("acctA", "acctB", "acctC")}),
            ("group of 3, A bad (picked first)", 3, {"acctA": "model_not_supported", "acctB": "ok", "acctC": "ok"})]
    litellm.callbacks = [Plug()]
    for name, var in variants.items():
        patch_provider(var)
        for sname, n, scripts in scen:
            await reset(scripts)
            router = Router(model_list=deployments(n), num_retries=4, enable_weighted_failover=True)
            out, dt = await one(router)
            await asyncio.sleep(0.3)
            print(json.dumps({"own_error_num_retries_0": VARIANT["own_error_no_retries"], "variant": name, "scenario": sname, "result": out, "elapsed_s": dt, "upstream": attempts(),
                              "failure_statuses": FAILURE_STATUSES[:], "alert_hits_402_403": sum(1 for s in FAILURE_STATUSES if s in (402, 403))}), flush=True)


async def r3():
    litellm.callbacks = [Plug()]
    for flag in (False, True):
        VARIANT["own_error_no_retries"] = flag
        await reset({})
        router = Router(model_list=deployments(3), num_retries=4, enable_weighted_failover=True)
        Plug.attempted.clear()
        # exhaust: pre-fill attempted for any call id by making the filter see no candidates
        class Empty(Plug):
            async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):
                return await Plug.async_filter_deployments(self, model, [], messages, request_kwargs, parent_otel_span)
        litellm.callbacks = [Empty()]
        out, dt = await one(router)
        print(json.dumps({"own_error_num_retries_0": flag, "result": out, "elapsed_s": dt, "filter_calls": len(FILTER_CALLS)}), flush=True)
        litellm.callbacks = [Plug()]


async def r4():
    patch_provider({"status": None})
    litellm.callbacks = []
    await reset({"acctA": "ok", "acctB": "ok", "acctC": "ok"})
    await one(Router(model_list=deployments(3), num_retries=0))  # warm-up: first call in the process pays one-off init
    for chat in (False, True):
        for cd in (None, 0):
            for label, plug in (("plugin present", True), ("no plugin", False)):
                litellm.callbacks = [Plug()] if plug else []
                await reset({a: "usage_limit" for a in ("acctA", "acctB", "acctC")})
                router = Router(model_list=deployments(3, cooldown_time=cd), num_retries=0, enable_weighted_failover=True)
                rows = []
                for n in range(4):
                    out, dt = await one(router, chat=chat)
                    rows.append(f"{out} {dt}s")
                print(json.dumps({"path": "chat" if chat else "responses", "deployment_cooldown_time": cd, "case": label, "requests": rows,
                                  "upstream_attempts": len(attempts()), "filter_healthy_counts": FILTER_CALLS[:]}), flush=True)


async def r5():
    """Own error as a final (non-retried) error after upstream failures: single group and three groups, upstream 429 and 5xx."""
    litellm.callbacks = [Plug()]
    patch_provider({"status": None})
    for cls in ("retryable", "final"):
        VARIANT["own_error_class"] = cls
        for sname, n, action in (("group of 1, upstream 429", 1, "usage_limit"), ("group of 3, upstream 429", 3, "usage_limit"),
                                 ("group of 1, upstream 503", 1, "overloaded"), ("group of 3, upstream 503", 3, "overloaded")):
            await reset({a: action for a in ("acctA", "acctB", "acctC")})
            router = Router(model_list=deployments(n), num_retries=4, enable_weighted_failover=True)
            out, dt = await one(router)
            print(json.dumps({"own_error_class": cls, "scenario": sname, "result": out, "elapsed_s": dt, "upstream": attempts(), "filter_calls": len(FILTER_CALLS)}), flush=True)


async def main():
    global PORT
    app = mock_upstream.app
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    PORT = site._server.sockets[0].getsockname()[1]
    which = sys.argv[1:] or ["r1", "r3", "r4", "r5"]
    for name in which:
        print(f"## {name}", flush=True)
        await {"r1": r1, "r3": r3, "r4": r4, "r5": r5}[name]()

asyncio.run(main())
