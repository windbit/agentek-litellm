import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from litellm.llms.chatgpt.codex_identity import codex_identity_headers
from litellm.llms.chatgpt.common_utils import (
    CHATGPT_API_BASE,
    CHATGPT_CLIENT_ID,
    CHATGPT_OAUTH_TOKEN_URL,
)

from ..model import Limits
from .base import (
    ErrorClass,
    Headers,
    ProbeResult,
    RefreshedTokens,
    RefreshOutcome,
    RefreshRejected,
)
from .chatgpt_classify import (
    STATUS_BAD_REQUEST,
    STATUS_UNAUTHORIZED,
    classify_error,
    classify_stream_failure,
    stream_outcome,
)
from .chatgpt_json import json_object, number_of, text_of
from .chatgpt_limits import limits_from_usage_payload, parse_limits

PROVIDER_ID = "chatgpt"

USAGE_URL = CHATGPT_API_BASE.removesuffix("/codex") + "/wham/usage"

RESPONSES_URL = CHATGPT_API_BASE + "/responses"

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


class ChatGPTProvider:
    id = PROVIDER_ID

    def __init__(self, transport: ProbeTransport, probe_model: str) -> None:
        self._transport = transport
        self._probe_model = probe_model

    def parse_limits(
        self, headers: Headers, body: Mapping[str, object] | None, *, now: float
    ) -> Limits | None:
        return parse_limits(headers, body, now=now)

    def classify_error(
        self, status: int, headers: Headers, body: str, *, now: float
    ) -> ErrorClass:
        return classify_error(status, headers, body, now=now)

    def classify_stream_failure(
        self, event: Mapping[str, object], *, now: float
    ) -> ErrorClass:
        return classify_stream_failure(event, now=now)

    async def probe_health(self, auth: ChatgptAuth, *, now: float) -> ProbeResult:
        reply = await self._transport.post_json(
            RESPONSES_URL, request_headers(auth), probe_payload(self._probe_model)
        )
        if reply.status != 200:
            return ProbeResult(
                ok=False,
                error=classify_error(reply.status, reply.headers, reply.body, now=now),
                limits=None,
            )
        failure = stream_outcome(reply.body, now)
        limits = parse_limits(reply.headers, None, now=now)
        return ProbeResult(ok=failure is None, error=failure, limits=limits)

    async def probe_usage(self, auth: ChatgptAuth, *, now: float) -> Limits | None:
        reply = await self._transport.get(USAGE_URL, request_headers(auth))
        if reply.status != 200:
            return None
        return limits_from_usage_payload(json_object(reply.body), now)

    async def refresh(self, refresh_token: str, *, now: float) -> RefreshOutcome:
        payload = {
            "client_id": CHATGPT_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": OAUTH_SCOPE,
        }
        reply = await self._transport.post_json(CHATGPT_OAUTH_TOKEN_URL, {}, payload)
        return refresh_outcome(reply, refresh_token, now)


def refresh_outcome(
    reply: HttpReply, previous_refresh_token: str, now: float
) -> RefreshOutcome:
    payload = json_object(reply.body)
    if reply.status == 200 and payload and isinstance(payload.get("access_token"), str):
        access_token = str(payload["access_token"])
        return RefreshedTokens(
            access_token=access_token,
            refresh_token=text_of(payload.get("refresh_token"))
            or previous_refresh_token,
            id_token=text_of(payload.get("id_token")),
            expires_at=expires_at(payload, access_token, now),
        )
    error = payload.get("error") if payload else None
    error_code = (
        text_of(error.get("code")) if isinstance(error, Mapping) else text_of(error)
    )
    permanent = (
        reply.status in (STATUS_BAD_REQUEST, STATUS_UNAUTHORIZED)
        and error_code in PERMANENT_OAUTH_ERRORS
    )
    return RefreshRejected(permanent=permanent)


def expires_at(
    payload: Mapping[str, object], access_token: str, now: float
) -> float | None:
    expires_in = number_of(payload.get("expires_in"))
    if expires_in is not None:
        return now + expires_in
    return jwt_expiry(access_token)


def jwt_expiry(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return None
    return number_of(claims.get("exp")) if isinstance(claims, dict) else None


def request_headers(auth: ChatgptAuth) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "content-type": "application/json",
        "accept": "text/event-stream",
        **codex_identity_headers(),
    }
    if auth.account_id:
        headers["ChatGPT-Account-Id"] = auth.account_id
    return headers


def probe_payload(model: str) -> dict[str, object]:
    return {
        "model": model,
        "instructions": PROBE_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": PROBE_INPUT_TEXT}],
            }
        ],
        "stream": True,
        "store": False,
        "max_output_tokens": 1,
    }
