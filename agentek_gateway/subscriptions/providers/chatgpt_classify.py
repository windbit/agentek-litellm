import re
from collections.abc import Mapping

from .base import (
    AccountBanned,
    AuthRejected,
    ErrorClass,
    Headers,
    LimitReached,
    ModelNotSupported,
    RequestRejected,
    Unclassified,
)
from .chatgpt_json import error_object, error_text, json_object, mapping, text_of
from .chatgpt_limits import (
    FULL_USAGE_PERCENT,
    USAGE_LIMIT_MARKER,
    limit_window_and_reset,
    parse_limits,
)

STATUS_OVERLOADED = 529

STATUS_SERVER_ERROR_MIN = 500

STATUS_UNAUTHORIZED = 401

STATUS_BAD_REQUEST = 400

STATUS_NOT_FOUND = 404

STATUS_UNPROCESSABLE = 422

STATUS_TOO_MANY_REQUESTS = 429

STATUS_PAYLOAD_TOO_LARGE = 413

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


def classify_error(
    status: int, headers: Headers, body: str, *, now: float
) -> ErrorClass:
    payload = json_object(body)
    error = error_object(payload)
    code = text_of(error.get("code")) if error else None
    kind = text_of(error.get("type")) if error else None
    message = error_text(payload, error, body)
    if code == "account_deactivated" or "has been deactivated" in message:
        return AccountBanned()
    if status == STATUS_TOO_MANY_REQUESTS:
        return classify_too_many_requests(kind, code, headers, payload, now)
    rejected = model_rejection(status, code, message)
    if rejected:
        return rejected
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
    error = stream_error(event)
    code = text_of(error.get("code")) if error else None
    kind = text_of(error.get("type")) if error else None
    if code == "account_deactivated":
        return AccountBanned()
    if USAGE_LIMIT_MARKER in (code, kind):
        return LimitReached(*limit_window_and_reset({}, error or {}, now))
    if code in OVERLOAD_CODES:
        return Unclassified(immediate=False, recognized=True)
    if code in REQUEST_ERROR_CODES:
        return RequestRejected(STATUS_BAD_REQUEST)
    return Unclassified(immediate=False, recognized=False)


def classify_too_many_requests(
    kind: str | None,
    code: str | None,
    headers: Headers,
    payload: Mapping[str, object] | None,
    now: float,
) -> ErrorClass:
    error = error_object(payload) or {}
    limits = parse_limits(headers, None, now=now)
    exhausted_by_headers = limits is not None and any(
        window and window.used_percent >= FULL_USAGE_PERCENT
        for window in (limits.five_hour, limits.weekly)
    )
    if USAGE_LIMIT_MARKER in (kind, code) or exhausted_by_headers:
        window, reset_at = limit_window_and_reset(headers, error, now)
        return LimitReached(window, reset_at)
    return Unclassified(immediate=False, recognized=False)


def model_rejection(
    status: int, code: str | None, message: str
) -> ModelNotSupported | None:
    matched = MODEL_NOT_SUPPORTED_PATTERN.search(message)
    if status == STATUS_BAD_REQUEST and matched:
        return ModelNotSupported(matched.group(1))
    if status == STATUS_NOT_FOUND and code == "model_not_found":
        model = re.search(r"`([^`]+)`", message)
        return ModelNotSupported(model.group(1) if model else None)
    return None


def stream_outcome(body: str, now: float) -> ErrorClass | None:
    """None when the stream ended with response.completed; otherwise the failure or an unrecognized cut-off."""
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        event = json_object(line.removeprefix("data:").strip())
        if not event:
            continue
        if event.get("type") in ("error", "response.failed"):
            return classify_stream_failure(event, now=now)
        if event.get("type") == "response.completed":
            return None
    return Unclassified(immediate=False, recognized=False)


def stream_error(event: Mapping[str, object]) -> Mapping[str, object] | None:
    response = event.get("response")
    if isinstance(response, Mapping) and isinstance(response.get("error"), Mapping):
        return mapping(response["error"])
    nested = event.get("error")
    if isinstance(nested, Mapping):
        return mapping(nested)
    return event if event.get("code") else None
