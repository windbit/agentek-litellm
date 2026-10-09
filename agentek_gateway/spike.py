"""Spike plugin: logs everything the subscription plugin would need to see (tasks 0.1, 0.3, 0.5, 0.8, 0.10)."""
import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import litellm
from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_logger import CustomLogger

SPIKE_DIR = os.environ.get("SPIKE_DIR", "/spikes")
LOG_PATH = os.path.join(SPIKE_DIR, "logs", "spike_events.jsonl")
CTL_PATH = os.path.join(SPIKE_DIR, "ctl.json")
STARTED_AT = time.time()
STATE: Dict[str, Any] = {"hook_ran_at": None, "prisma_ready_at": None, "router_ready_at": None, "bg_ticks": 0}

_ctl_cache: Dict[str, Any] = {"mtime": 0.0, "data": {}}


def ctl() -> dict:
    try:
        mtime = os.stat(CTL_PATH).st_mtime
    except FileNotFoundError:
        return {}
    if mtime != _ctl_cache["mtime"]:
        with open(CTL_PATH) as handle:
            _ctl_cache["data"] = json.load(handle)
        _ctl_cache["mtime"] = mtime
    return _ctl_cache["data"]


def emit(event: str, **fields: Any) -> None:
    rec = {"t": round(time.time(), 3), "pid": os.getpid(), "event": event, **fields}
    old = os.umask(0)
    try:
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    finally:
        os.umask(old)
    with os.fdopen(fd, "a") as handle:
        handle.write(json.dumps(rec, default=str) + "\n")


def find_paths(obj: Any, needle: str, path: str = "", depth: int = 0, substr: bool = False) -> List[str]:
    found: List[str] = []
    if depth > 6:
        return found
    if isinstance(obj, dict):
        for key, value in obj.items():
            sub = f"{path}.{key}" if path else str(key)
            if (key == needle) or (substr and isinstance(key, str) and needle in key.lower()):
                found.append(sub)
            found.extend(find_paths(value, needle, sub, depth + 1, substr))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj[:20]):
            found.extend(find_paths(value, needle, f"{path}[{idx}]", depth + 1, substr))
    return found


def metric(kind: str, name: str, doc: str, labels: Optional[list] = None):
    """Idempotent prometheus registration: a second startup in the same process reuses the collector."""
    from prometheus_client import REGISTRY, Counter, Gauge

    existing = REGISTRY._names_to_collectors.get(name) or REGISTRY._names_to_collectors.get(name + "_total")
    if existing is not None:
        return existing
    cls = {"counter": Counter, "gauge": Gauge}[kind]
    return cls(name, doc, labels or [])


import contextvars

ATTEMPTED: Dict[str, set] = {}
INTERNAL_ERRORS: set = set()
ATTEMPT: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("agentek_attempt", default=None)


def patch_provider_error_observer() -> None:
    """Observer on the chatgpt provider error path: sees EVERY upstream error response (spike 0.3 option C)."""
    from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

    if getattr(ChatGPTResponsesAPIConfig, "_agentek_wrapped", False):
        return
    original = ChatGPTResponsesAPIConfig.get_error_class

    def wrapped(self, error_message, status_code, headers):
        attempt = ATTEMPT.get() or {}
        emit(
            "provider_error",
            status=status_code,
            call_id=attempt.get("call_id"),
            deployment_id=attempt.get("deployment_id"),
            headers={k: v for k, v in dict(headers or {}).items() if k.lower().startswith(("x-codex", "retry-after"))},
            message=str(error_message)[:200],
        )
        remap = ctl().get("remap_not_supported_to")
        if remap and status_code == 400 and "is not supported when using Codex" in str(error_message):
            emit("provider_error_remapped", frm=status_code, to=remap, call_id=attempt.get("call_id"))
            status_code = remap
        return original(self, error_message, status_code, headers)

    ChatGPTResponsesAPIConfig.get_error_class = wrapped
    ChatGPTResponsesAPIConfig._agentek_wrapped = True


class SpikeLogger(CustomLogger):
    def __init__(self) -> None:
        super().__init__()
        self.calls = metric("counter", "agentek_spike_filter_calls", "filter calls")

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):
        self.calls.inc()
        rk = request_kwargs or {}
        md = rk.get("metadata") if isinstance(rk.get("metadata"), dict) else None
        lmd = rk.get("litellm_metadata") if isinstance(rk.get("litellm_metadata"), dict) else None
        ids = [(d.get("model_info") or {}).get("id") for d in healthy_deployments]
        emit(
            "filter",
            model=model,
            call_id=rk.get("litellm_call_id"),
            rk_id=id(rk),
            rk_keys=sorted(rk.keys()),
            messages_is_none=messages is None,
            metadata_keys=sorted(md.keys()) if md is not None else None,
            litellm_metadata_keys=sorted(lmd.keys()) if lmd is not None else None,
            user_api_key_metadata=(md or lmd or {}).get("user_api_key_metadata"),
            tags=(md or lmd or {}).get("tags"),
            prompt_cache_key_paths=find_paths(rk, "prompt_cache_key"),
            prompt_cache_key=rk.get("prompt_cache_key"),
            excluded=rk.get("_excluded_deployment_ids"),
            target_order=rk.get("_target_order"),
            failover_excluded_meta=(md or lmd or {}).get("_failover_excluded_ids"),
            num_retries=rk.get("num_retries"),
            healthy_ids=ids,
        )
        mode = ctl().get("filter_mode", "log")
        if mode == "log":
            return healthy_deployments
        excluded = set(rk.get("_excluded_deployment_ids") or [])
        if ctl().get("track_attempts"):
            excluded |= ATTEMPTED.get(rk.get("litellm_call_id"), set())
        if mode == "pick_order":
            for wanted in ctl().get("pick_order", []):
                for dep in healthy_deployments:
                    dep_id = (dep.get("model_info") or {}).get("id")
                    if dep_id == wanted and dep_id not in excluded:
                        emit("filter_pick", call_id=rk.get("litellm_call_id"), picked=dep_id)
                        return [dep]
            emit("filter_pick", call_id=rk.get("litellm_call_id"), picked=None)
            exc = litellm.RateLimitError(
                message="agentek: no available subscriptions (spike)",
                llm_provider="agentek",
                model=model,
                response=_resp_with_retry_after(10),
            )
            exc.agentek_internal = True
            variant = ctl().get("retry_after_variant", "response")
            if variant == "attr":
                exc.headers = {"retry-after": "10"}
            INTERNAL_ERRORS.add(rk.get("litellm_call_id"))
            raise exc
        return healthy_deployments

    async def async_pre_call_deployment_hook(self, kwargs: Dict[str, Any], call_type: Optional[Any]) -> Optional[dict]:
        md_name = "litellm_metadata" if isinstance(kwargs.get("litellm_metadata"), dict) else "metadata"
        md = kwargs.get(md_name) if isinstance(kwargs.get(md_name), dict) else {}
        tags_before = list(md.get("tags") or [])
        cred = kwargs.get("litellm_credential_name")
        mode = ctl().get("rewrite_tags")
        if mode and cred:
            keep = [t for t in tags_before if not (isinstance(t, str) and t.startswith("Credential: "))]
            keep.append(f"Credential: {cred}")
            if mode == "inplace" and isinstance(md.get("tags"), list):
                md["tags"][:] = keep
            else:
                md["tags"] = keep
        ATTEMPTED.setdefault(kwargs.get("litellm_call_id"), set()).add((md.get("model_info") or {}).get("id"))
        ATTEMPT.set({"call_id": kwargs.get("litellm_call_id"), "deployment_id": (md.get("model_info") or {}).get("id")})
        emit(
            "pre_call_hook",
            call_type=str(call_type),
            call_id=kwargs.get("litellm_call_id"),
            md_name=md_name,
            deployment_id=(md.get("model_info") or {}).get("id"),
            cred_kw=cred,
            tags_before=tags_before,
            tags_after=list(md.get("tags") or []),
            kw_keys=sorted(kwargs.keys()),
            metadata_same_obj_as_litellm_params=None,
        )
        pck = kwargs.get("prompt_cache_key")
        if ctl().get("session_from_pck") and pck:
            import hashlib

            modified = dict(kwargs)
            modified["litellm_session_id"] = "agentek-" + hashlib.sha256(str(pck).encode()).hexdigest()[:16]
            emit("session_override", call_id=kwargs.get("litellm_call_id"), session_id=modified["litellm_session_id"])
            return modified
        return None

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        hidden = getattr(response_obj, "_hidden_params", None) or {}
        add = hidden.get("additional_headers") or {}
        std = kwargs.get("standard_logging_object") or {}
        emit(
            "success",
            call_id=kwargs.get("litellm_call_id"),
            stream=kwargs.get("stream"),
            deployment_id=((kwargs.get("litellm_params") or {}).get("model_info") or {}).get("id"),
            codex_headers={k: v for k, v in add.items() if "codex" in k.lower()},
            codex_paths=find_paths(kwargs, "x-codex", substr=True)[:6],
            resp_type=type(response_obj).__name__,
            resp_hidden_codex=find_paths(hidden, "x-codex", substr=True)[:4],
            hidden_keys=sorted(hidden.keys()),
            std_hidden_additional=sorted(((std.get("hidden_params") or {}).get("additional_headers") or {}).keys()),
            request_tags=std.get("request_tags"),
        )

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        exc = kwargs.get("exception")
        std = kwargs.get("standard_logging_object") or {}
        emit(
            "failure",
            call_id=kwargs.get("litellm_call_id"),
            stream=kwargs.get("stream"),
            deployment_id=((kwargs.get("litellm_params") or {}).get("model_info") or {}).get("id"),
            exc_class=type(exc).__name__ if exc is not None else None,
            status=getattr(exc, "status_code", None),
            exc_text=str(exc)[:300] if exc is not None else None,
            exc_headers=_exc_headers(exc),
            exc_body=_exc_body(exc),
            exc_dump=_exc_dump(exc),
            error_information=std.get("error_information"),
            elapsed=round(time.time() - start_time.timestamp(), 3) if hasattr(start_time, "timestamp") else None,
        )

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
        call_id = request_data.get("litellm_call_id")
        count = 0
        kinds: List[str] = []
        status = "completed"
        try:
            async for chunk in response:
                count += 1
                kind = chunk.get("type") if isinstance(chunk, dict) else getattr(chunk, "type", None)
                if kind is None and isinstance(chunk, (str, bytes)):
                    text = chunk.decode() if isinstance(chunk, bytes) else chunk
                    kind = "str:" + text[:60].replace("\n", " ")
                if len(kinds) < 12:
                    kinds.append(str(kind))
                if str(kind).lower().endswith(("error", "failed")):
                    payload = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
                    emit("stream_error_chunk", call_id=call_id, chunk_type=type(chunk).__name__, payload=json.dumps(payload, default=str)[:700])
                yield chunk
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except GeneratorExit:
            status = "generator_exit"
            raise
        except BaseException as err:
            status = "error:" + type(err).__name__
            raise
        finally:
            emit("stream_end", call_id=call_id, status=status, chunks=count, kinds=kinds)

    async def async_post_call_response_headers_hook(self, data, user_api_key_dict, response, request_headers=None, litellm_call_info=None):
        hidden = getattr(response, "_hidden_params", None) or {}
        add = hidden.get("additional_headers") or {}
        if response is None and data.get("litellm_call_id") in INTERNAL_ERRORS and ctl().get("retry_after_variant") == "hook":
            emit("response_headers_hook", call_id=data.get("litellm_call_id"), injected_retry_after=True)
            return {"retry-after": "10"}
        emit(
            "response_headers_hook",
            call_id=data.get("litellm_call_id"),
            resp_type=type(response).__name__,
            stream=data.get("stream"),
            codex_n=len([k for k in add if "codex" in k.lower()]),
            call_info=litellm_call_info,
        )
        return None

    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict, traceback_str=None):
        emit(
            "proxy_failure_hook",
            call_id=request_data.get("litellm_call_id"),
            exc_class=type(original_exception).__name__,
            status=getattr(original_exception, "status_code", None),
            text=str(original_exception)[:200],
        )


def _resp_with_retry_after(seconds: int):
    import httpx

    return httpx.Response(status_code=429, headers={"retry-after": str(seconds)}, request=httpx.Request("POST", "http://agentek"))


def _exc_headers(exc: Any) -> Optional[dict]:
    headers = getattr(exc, "headers", None)
    if headers is None:
        resp = getattr(exc, "response", None)
        headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    return {k: v for k, v in dict(headers).items() if k.lower().startswith(("x-codex", "retry-after", "llm_provider-x-codex"))}


def _exc_dump(exc: Any) -> dict:
    """Everything an exception carries: used to decide where x-codex-* / resets_at can be read (task 0.2)."""
    out: Dict[str, Any] = {"mro": [c.__name__ for c in type(exc).__mro__[:5]], "attrs": sorted(k for k in vars(exc).keys())}
    for name in ("headers", "litellm_response_headers", "body", "code", "param", "type", "llm_provider", "message"):
        val = getattr(exc, name, None)
        if val not in (None, "", {}):
            out[name] = dict(val) if hasattr(val, "items") else str(val)[:200]
    resp = getattr(exc, "response", None)
    if resp is not None:
        out["response_status"] = getattr(resp, "status_code", None)
        try:
            out["response_headers"] = dict(resp.headers)
            out["response_text"] = resp.text[:300]
        except Exception as err:
            out["response_err"] = repr(err)
    return out


def _exc_body(exc: Any) -> Any:
    body = getattr(exc, "body", None)
    if body:
        return body
    resp = getattr(exc, "response", None)
    try:
        return resp.text[:300] if resp is not None else None
    except Exception:
        return None


async def _wait_ready() -> None:
    from litellm.proxy import proxy_server

    while STATE["router_ready_at"] is None or STATE["prisma_ready_at"] is None:
        STATE["bg_ticks"] += 1
        if STATE["prisma_ready_at"] is None and getattr(proxy_server, "prisma_client", None) is not None:
            STATE["prisma_ready_at"] = round(time.time() - STARTED_AT, 2)
        if STATE["router_ready_at"] is None and getattr(proxy_server, "llm_router", None) is not None:
            STATE["router_ready_at"] = round(time.time() - STARTED_AT, 2)
        await asyncio.sleep(0.2)
    emit("ready", **STATE)


def startup() -> None:
    from fastapi import APIRouter, Depends
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
    from litellm.proxy.proxy_server import app

    emit("startup_hook_begin", prisma_client_is_none=True)
    STATE["hook_ran_at"] = round(time.time() - STARTED_AT, 2)

    if not any(isinstance(cb, SpikeLogger) for cb in litellm.callbacks):
        litellm.callbacks.append(SpikeLogger())

    router = APIRouter(prefix="/agentek/spike")

    @router.get("/ping")
    async def ping():
        return {"ok": True, "state": STATE}

    @router.get("/whoami")
    async def whoami(auth: UserAPIKeyAuth = Depends(user_api_key_auth)):
        return {"role": str(auth.user_role), "key_alias": auth.key_alias}

    if not STATE.get("router_included"):
        app.include_router(router)
        STATE["router_included"] = True
    patch_provider_error_observer()
    asyncio.get_running_loop().create_task(_wait_ready())
    metric("gauge", "agentek_spike_started", "set at startup").set(time.time())
