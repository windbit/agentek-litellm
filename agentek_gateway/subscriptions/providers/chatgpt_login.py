from dataclasses import dataclass

from litellm.llms.chatgpt.common_utils import (
    CHATGPT_AUTH_BASE,
    CHATGPT_CLIENT_ID,
    CHATGPT_DEVICE_CODE_URL,
    CHATGPT_DEVICE_TOKEN_URL,
    CHATGPT_DEVICE_VERIFY_URL,
    CHATGPT_OAUTH_TOKEN_URL,
)

from .chatgpt import ChatgptAuth, HttpReply, ProbeTransport
from .chatgpt_json import json_object, text_of
from .chatgpt_profile import account_id_of, jwt_expiry

DEVICE_REDIRECT_URI = f"{CHATGPT_AUTH_BASE}/deviceauth/callback"
PENDING_STATUSES = frozenset({403, 404})
JSON_HEADERS = {"Content-Type": "application/json"}


class ProviderLoginError(Exception):
    """A login step the provider rejected; the message never carries tokens or response bodies."""


@dataclass(frozen=True, slots=True)
class DeviceLogin:
    device_auth_id: str
    user_code: str
    verify_url: str


class ChatgptLogin:
    """Device-code sign-in: the operator enters the user code in a browser while the gateway polls."""

    def __init__(self, transport: ProbeTransport) -> None:
        self._transport = transport

    async def start(self) -> DeviceLogin:
        reply = await self._transport.post_json(
            CHATGPT_DEVICE_CODE_URL, JSON_HEADERS, {"client_id": CHATGPT_CLIENT_ID}
        )
        payload = json_object(reply.body) or {}
        device_auth_id = text_of(payload.get("device_auth_id"))
        user_code = text_of(payload.get("user_code")) or text_of(
            payload.get("usercode")
        )
        if reply.status != 200 or not device_auth_id or not user_code:
            raise ProviderLoginError(f"device code request failed, HTTP {reply.status}")
        return DeviceLogin(device_auth_id, user_code, CHATGPT_DEVICE_VERIFY_URL)

    async def poll(self, device_auth_id: str, user_code: str) -> ChatgptAuth | None:
        """None while the operator has not finished the browser step."""
        reply = await self._transport.post_json(
            CHATGPT_DEVICE_TOKEN_URL,
            JSON_HEADERS,
            {"device_auth_id": device_auth_id, "user_code": user_code},
        )
        if reply.status in PENDING_STATUSES:
            return None
        payload = json_object(reply.body) or {}
        code = text_of(payload.get("authorization_code"))
        verifier = text_of(payload.get("code_verifier"))
        if reply.status != 200 or not code or not verifier:
            raise ProviderLoginError(f"device login poll failed, HTTP {reply.status}")
        return await self._exchange_code(code, verifier)

    async def _exchange_code(self, code: str, verifier: str) -> ChatgptAuth:
        reply = await self._transport.post_form(
            CHATGPT_OAUTH_TOKEN_URL,
            {},
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DEVICE_REDIRECT_URI,
                "client_id": CHATGPT_CLIENT_ID,
                "code_verifier": verifier,
            },
        )
        return auth_from_exchange(reply)


def auth_from_exchange(reply: HttpReply) -> ChatgptAuth:
    payload = json_object(reply.body) or {}
    access = text_of(payload.get("access_token"))
    refresh = text_of(payload.get("refresh_token"))
    id_token = text_of(payload.get("id_token"))
    if reply.status != 200 or not access or not refresh or not id_token:
        raise ProviderLoginError(f"token exchange failed, HTTP {reply.status}")
    return ChatgptAuth(
        access_token=access,
        refresh_token=refresh,
        id_token=id_token,
        expires_at=jwt_expiry(access),
        account_id=account_id_of(id_token) or account_id_of(access),
    )
