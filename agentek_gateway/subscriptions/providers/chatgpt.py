import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from litellm.llms.chatgpt.codex_identity import codex_identity_headers
from litellm.llms.chatgpt.common_utils import (
    CHATGPT_API_BASE,
    CHATGPT_CLIENT_ID,
    CHATGPT_OAUTH_TOKEN_URL,
)

from ..events import LimitWindow
from ..model import Limits, Window
from .base import (
    AccountBanned,
    AuthRejected,
    ErrorClass,
    Headers,
    LimitReached,
    ModelNotSupported,
    ProbeResult,
    RefreshedTokens,
    RefreshOutcome,
    RefreshRejected,
    RequestRejected,
    Unclassified,
)

PROVIDER_ID = "chatgpt"
HEADER_PREFIX = "llm_provider-"
USAGE_URL = CHATGPT_API_BASE.removesuffix("/codex") + "/wham/usage"
RESPONSES_URL = CHATGPT_API_BASE + "/responses"
FIVE_HOUR_MAX_WINDOW_MINUTES = 24 * 60
FULL_USAGE_PERCENT = 100.0
STATUS_OVERLOADED = 529
STATUS_SERVER_ERROR_MIN = 500
STATUS_UNAUTHORIZED = 401
STATUS_BAD_REQUEST = 400
STATUS_NOT_FOUND = 404
STATUS_UNPROCESSABLE = 422
STATUS_TOO_MANY_REQUESTS = 429
STATUS_PAYLOAD_TOO_LARGE = 413
LOG_BODY_LIMIT_BYTES = 2048
DROPPED_LOG_HEADERS = frozenset(
    {
        "authorization",
        "set-cookie",
        "cookie",
        "chatgpt-account-id",
        "x-codex-turn-state",
    }
)
PROBE_MODEL = "gpt-5.5"
PROBE_INSTRUCTIONS = "Reply with a single character."
PROBE_INPUT_TEXT = "ping"
OAUTH_SCOPE = "openid profile email"
PERMANENT_OAUTH_ERRORS = frozenset(
    {
        "invalid_grant",
        "refresh_token_reused",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "invalid_client",
    }
)
USAGE_LIMIT_MARKER = "usage_limit_reached"
OVERLOAD_CODES = frozenset(
    {"server_is_overloaded", "server_error", "service_unavailable", "slow_down"}
)
REQUEST_ERROR_CODES = frozenset(
    {
        "invalid_prompt",
        "context_length_exceeded",
        "invalid_request_error",
        "unsupported_parameter",
    }
)
MODEL_NOT_SUPPORTED_PATTERN = re.compile(
    r"The '([^']+)' model is not supported when using Codex"
)
SECRET_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    re.compile(r"\bsk-[A-Za-z0-9*_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
)
MASK = "***"


@dataclass(frozen=True, slots=True)
class HttpReply:
    status: int
    headers: Headers
    body: str


class ProbeTransport(Protocol):
    async def post_json(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, object]
    ) -> HttpReply: ...

    async def get(self, url: str, headers: Mapping[str, str]) -> HttpReply: ...


@dataclass(frozen=True, slots=True)
class ChatgptAuth:
    access_token: str
    refresh_token: str
    account_id: str | None = None
    id_token: str | None = None
    expires_at: float | None = None


def normalize_headers(headers: Headers) -> dict[str, str]:
    return {
        name.lower().removeprefix(HEADER_PREFIX): value
        for name, value in headers.items()
    }


def parse_limits(
    headers: Headers, body: Mapping[str, object] | None, *, now: float
) -> Limits | None:
    normalized = normalize_headers(headers)
    primary = _header_window(normalized, "primary", now)
    secondary = _header_window(normalized, "secondary", now)
    five_hour = weekly = None
    for label, (window, minutes) in (("primary", primary), ("secondary", secondary)):
        if window is None:
            continue
        slot = _slot_for(label, minutes, secondary_present=secondary[0] is not None)
        if slot is LimitWindow.FIVE_HOUR:
            five_hour = window
        else:
            weekly = window
    if five_hour or weekly:
        return Limits(five_hour=five_hour, weekly=weekly)
    return _limits_from_error_body(body, now)


def classify_error(
    status: int, headers: Headers, body: str, *, now: float
) -> ErrorClass:
    payload = _json_object(body)
    error = _error_object(payload)
    code = _text(error.get("code")) if error else None
    kind = _text(error.get("type")) if error else None
    message = _error_text(payload, error, body)
    if code == "account_deactivated" or "has been deactivated" in message:
        return AccountBanned()
    if status == STATUS_TOO_MANY_REQUESTS:
        return _classify_too_many_requests(kind, code, headers, payload, now)
    model_error = _model_error(status, code, message)
    if model_error:
        return model_error
    if status == STATUS_UNAUTHORIZED:
        return AuthRejected()
    if status in (
        STATUS_BAD_REQUEST,
        STATUS_NOT_FOUND,
        STATUS_UNPROCESSABLE,
        STATUS_PAYLOAD_TOO_LARGE,
    ):
        return RequestRejected(status)
    if code in REQUEST_ERROR_CODES:
        return RequestRejected(status)
    return Unclassified(
        immediate=status == STATUS_OVERLOADED,
        recognized=status >= STATUS_SERVER_ERROR_MIN or code in OVERLOAD_CODES,
    )


def classify_stream_failure(event: Mapping[str, object], *, now: float) -> ErrorClass:
    error = _stream_error(event)
    code = _text(error.get("code")) if error else None
    kind = _text(error.get("type")) if error else None
    if code == "account_deactivated":
        return AccountBanned()
    if USAGE_LIMIT_MARKER in (code, kind):
        return LimitReached(*_limit_window_and_reset({}, error or {}, now))
    if code in OVERLOAD_CODES:
        return Unclassified(immediate=False, recognized=True)
    if code in REQUEST_ERROR_CODES:
        return RequestRejected(STATUS_BAD_REQUEST)
    return Unclassified(immediate=False, recognized=False)


def redact_for_log(
    subscription_name: str, status: int, headers: Headers, body: str
) -> str:
    kept = {
        name: value
        for name, value in headers.items()
        if name.lower() not in DROPPED_LOG_HEADERS
    }
    raw = body.encode("utf-8", errors="replace")[:LOG_BODY_LIMIT_BYTES].decode(
        "utf-8", errors="replace"
    )
    return json.dumps(
        {
            "subscription": subscription_name,
            "status": status,
            "headers": kept,
            "body": mask_secrets(raw),
        },
        ensure_ascii=False,
    )


def mask_secrets(text: str) -> str:
    masked = text
    for pattern in SECRET_PATTERNS:
        masked = pattern.sub(MASK, masked)
    return masked


class ChatGPTProvider:
    id = PROVIDER_ID

    def __init__(self, transport: ProbeTransport) -> None:
        self._transport = transport

    def parse_limits(
        self, headers: Headers, body: Mapping[str, object] | None, *, now: float
    ) -> Limits | None:
        return parse_limits(headers, body, now=now)

    def classify_error(
        self, status: int, headers: Headers, body: str, *, now: float
    ) -> ErrorClass:
        return classify_error(status, headers, body, now=now)

    async def probe_health(self, auth: ChatgptAuth, *, now: float) -> ProbeResult:
        reply = await self._transport.post_json(
            RESPONSES_URL, _request_headers(auth), _probe_payload()
        )
        if reply.status != 200:
            return ProbeResult(
                ok=False,
                error=classify_error(reply.status, reply.headers, reply.body, now=now),
                limits=None,
            )
        failure = _first_stream_failure(reply.body, now)
        limits = parse_limits(reply.headers, None, now=now)
        return ProbeResult(ok=failure is None, error=failure, limits=limits)

    async def probe_usage(self, auth: ChatgptAuth, *, now: float) -> Limits | None:
        reply = await self._transport.get(USAGE_URL, _request_headers(auth))
        if reply.status != 200:
            return None
        return _limits_from_usage_payload(_json_object(reply.body), now)

    async def refresh(self, refresh_token: str, *, now: float) -> RefreshOutcome:
        payload = {
            "client_id": CHATGPT_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": OAUTH_SCOPE,
        }
        reply = await self._transport.post_json(CHATGPT_OAUTH_TOKEN_URL, {}, payload)
        return _refresh_outcome(reply, refresh_token, now)


def _header_window(
    headers: Mapping[str, str], label: str, now: float
) -> tuple[Window | None, float]:
    used = _number(headers.get(f"x-codex-{label}-used-percent"))
    minutes = _number(headers.get(f"x-codex-{label}-window-minutes")) or 0.0
    reset_at = _number(headers.get(f"x-codex-{label}-reset-at"))
    after = _number(headers.get(f"x-codex-{label}-reset-after-seconds"))
    if reset_at is None and after:
        reset_at = now + after
    if used is None or reset_at is None:
        return None, minutes
    return Window(used, reset_at), minutes


def _slot_for(label: str, minutes: float, *, secondary_present: bool) -> LimitWindow:
    if minutes > 0:
        return (
            LimitWindow.FIVE_HOUR
            if minutes <= FIVE_HOUR_MAX_WINDOW_MINUTES
            else LimitWindow.WEEKLY
        )
    if label == "primary" and secondary_present:
        return LimitWindow.FIVE_HOUR
    return LimitWindow.WEEKLY


def _limits_from_error_body(
    body: Mapping[str, object] | None, now: float
) -> Limits | None:
    error = _error_object(body)
    if not error or USAGE_LIMIT_MARKER not in (error.get("type"), error.get("code")):
        return None
    window, reset_at = _limit_window_and_reset({}, error, now)
    if reset_at is None:
        return None
    exhausted = Window(FULL_USAGE_PERCENT, reset_at)
    return (
        Limits(five_hour=exhausted)
        if window is LimitWindow.FIVE_HOUR
        else Limits(weekly=exhausted)
    )


def _classify_too_many_requests(
    kind: str | None,
    code: str | None,
    headers: Headers,
    payload: Mapping[str, object] | None,
    now: float,
) -> ErrorClass:
    error = _error_object(payload) or {}
    limits = parse_limits(headers, None, now=now)
    exhausted_by_headers = limits is not None and any(
        window and window.used_percent >= FULL_USAGE_PERCENT
        for window in (limits.five_hour, limits.weekly)
    )
    if USAGE_LIMIT_MARKER in (kind, code) or exhausted_by_headers:
        window, reset_at = _limit_window_and_reset(headers, error, now)
        return LimitReached(window, reset_at)
    return Unclassified(immediate=False, recognized=False)


def _limit_window_and_reset(
    headers: Headers, error: Mapping[str, object], now: float
) -> tuple[LimitWindow, float | None]:
    minutes = _number(error.get("limit_window_minutes"))
    reset_at = _number(error.get("resets_at"))
    if reset_at is None:
        after = _number(error.get("resets_in_seconds"))
        reset_at = now + after if after is not None else None
    limits = parse_limits(headers, None, now=now)
    exhausted = _exhausted_window(limits)
    if reset_at is None and exhausted:
        reset_at = exhausted[1].reset_at
    return _window_kind(minutes, exhausted[0] if exhausted else None), reset_at


def _exhausted_window(limits: Limits | None) -> tuple[LimitWindow, Window] | None:
    if limits is None:
        return None
    for kind, window in (
        (LimitWindow.WEEKLY, limits.weekly),
        (LimitWindow.FIVE_HOUR, limits.five_hour),
    ):
        if window and window.used_percent >= FULL_USAGE_PERCENT:
            return kind, window
    return None


def _window_kind(
    minutes: float | None, from_headers: LimitWindow | None
) -> LimitWindow:
    if minutes:
        return (
            LimitWindow.FIVE_HOUR
            if minutes <= FIVE_HOUR_MAX_WINDOW_MINUTES
            else LimitWindow.WEEKLY
        )
    return from_headers or LimitWindow.UNKNOWN


def _model_error(
    status: int, code: str | None, message: str
) -> ModelNotSupported | None:
    matched = MODEL_NOT_SUPPORTED_PATTERN.search(message)
    if status == STATUS_BAD_REQUEST and matched:
        return ModelNotSupported(matched.group(1))
    if status == STATUS_NOT_FOUND and code == "model_not_found":
        model = re.search(r"`([^`]+)`", message)
        return ModelNotSupported(model.group(1) if model else None)
    return None


def _first_stream_failure(body: str, now: float) -> ErrorClass | None:
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        event = _json_object(line.removeprefix("data:").strip())
        if event and event.get("type") in ("error", "response.failed"):
            return classify_stream_failure(event, now=now)
    return None


def _stream_error(event: Mapping[str, object]) -> Mapping[str, object] | None:
    response = event.get("response")
    if isinstance(response, Mapping) and isinstance(response.get("error"), Mapping):
        return _mapping(response["error"])
    nested = event.get("error")
    if isinstance(nested, Mapping):
        return _mapping(nested)
    return event if event.get("code") else None


def _limits_from_usage_payload(
    payload: Mapping[str, object] | None, now: float
) -> Limits | None:
    rate_limit = _mapping(payload.get("rate_limit")) if payload else None
    if not rate_limit:
        return None
    five_hour = weekly = None
    for key in ("primary_window", "secondary_window"):
        raw = _mapping(rate_limit.get(key))
        window = _usage_window(raw, now) if raw else None
        if window is None or raw is None:
            continue
        seconds = _number(raw.get("limit_window_seconds")) or 0.0
        if 0 < seconds / 60 <= FIVE_HOUR_MAX_WINDOW_MINUTES:
            five_hour = window
        else:
            weekly = window
    return Limits(five_hour=five_hour, weekly=weekly) if (five_hour or weekly) else None


def _usage_window(raw: Mapping[str, object], now: float) -> Window | None:
    used = _number(raw.get("used_percent"))
    reset_at = _number(raw.get("reset_at"))
    after = _number(raw.get("reset_after_seconds"))
    if reset_at is None and after is not None:
        reset_at = now + after
    if used is None or reset_at is None:
        return None
    return Window(used, reset_at)


def _refresh_outcome(
    reply: HttpReply, previous_refresh_token: str, now: float
) -> RefreshOutcome:
    payload = _json_object(reply.body)
    if reply.status == 200 and payload and isinstance(payload.get("access_token"), str):
        access_token = str(payload["access_token"])
        return RefreshedTokens(
            access_token=access_token,
            refresh_token=_text(payload.get("refresh_token")) or previous_refresh_token,
            id_token=_text(payload.get("id_token")),
            expires_at=_expires_at(payload, access_token, now),
        )
    error = payload.get("error") if payload else None
    error_code = (
        _text(error.get("code")) if isinstance(error, Mapping) else _text(error)
    )
    permanent = (
        reply.status in (STATUS_BAD_REQUEST, STATUS_UNAUTHORIZED)
        and error_code in PERMANENT_OAUTH_ERRORS
    )
    return RefreshRejected(permanent=permanent)


def _expires_at(
    payload: Mapping[str, object], access_token: str, now: float
) -> float | None:
    expires_in = _number(payload.get("expires_in"))
    if expires_in is not None:
        return now + expires_in
    return _jwt_expiry(access_token)


def _jwt_expiry(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return None
    return _number(claims.get("exp")) if isinstance(claims, dict) else None


def _request_headers(auth: ChatgptAuth) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "content-type": "application/json",
        "accept": "text/event-stream",
        **codex_identity_headers(),
    }
    if auth.account_id:
        headers["ChatGPT-Account-Id"] = auth.account_id
    return headers


def _probe_payload() -> dict[str, object]:
    return {
        "model": PROBE_MODEL,
        "instructions": PROBE_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": PROBE_INPUT_TEXT}],
            }
        ],
        "stream": True,
        "store": False,
    }


def _json_object(text: str) -> Mapping[str, object] | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return _mapping(parsed)


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _error_object(payload: Mapping[str, object] | None) -> Mapping[str, object] | None:
    return _mapping(payload.get("error")) if payload else None


def _error_text(
    payload: Mapping[str, object] | None, error: Mapping[str, object] | None, raw: str
) -> str:
    detail = _text(payload.get("detail")) if payload else None
    message = _text(error.get("message")) if error else None
    return detail or message or raw[:LOG_BODY_LIMIT_BYTES]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
