import base64
import json
from collections.abc import Mapping

import pytest

from agentek_gateway.subscriptions.providers.chatgpt import HttpReply
from agentek_gateway.subscriptions.providers.chatgpt_login import (
    DEVICE_REDIRECT_URI,
    ChatgptLogin,
    ProviderLoginError,
)
from agentek_gateway.subscriptions.providers.chatgpt_profile import (
    AUTH_CLAIM,
    profile_of,
)
from litellm.llms.chatgpt.common_utils import (
    CHATGPT_DEVICE_CODE_URL,
    CHATGPT_DEVICE_TOKEN_URL,
    CHATGPT_DEVICE_VERIFY_URL,
    CHATGPT_OAUTH_TOKEN_URL,
)

EXPIRES = 4_000_000_000


def jwt(claims: Mapping[str, object]) -> str:
    def part(value: Mapping[str, object]) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


class Script:
    def __init__(self, *replies: HttpReply) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, Mapping[str, object]]] = []

    async def post_json(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, object]
    ) -> HttpReply:
        self.calls.append(("json", url, payload))
        return self.replies.pop(0)

    async def post_form(
        self, url: str, headers: Mapping[str, str], form: Mapping[str, str]
    ) -> HttpReply:
        self.calls.append(("form", url, form))
        return self.replies.pop(0)


def reply(status: int, body: object = "") -> HttpReply:
    text = body if isinstance(body, str) else json.dumps(body)
    return HttpReply(status, {}, text)


async def test_starting_a_login_returns_the_code_to_show_and_the_verification_page() -> (
    None
):
    transport = Script(reply(200, {"device_auth_id": "dev-1", "user_code": "AB-CD"}))

    login = await ChatgptLogin(transport).start()  # type: ignore[arg-type]

    assert (login.device_auth_id, login.user_code, login.verify_url) == (
        "dev-1",
        "AB-CD",
        CHATGPT_DEVICE_VERIFY_URL,
    )
    assert transport.calls[0][:2] == ("json", CHATGPT_DEVICE_CODE_URL)


async def test_a_rejected_code_request_fails_without_echoing_the_body() -> None:
    transport = Script(reply(500, "internal secret-ish body"))

    with pytest.raises(ProviderLoginError) as error:
        await ChatgptLogin(transport).start()  # type: ignore[arg-type]

    assert "secret-ish" not in str(error.value)


@pytest.mark.parametrize("status", [403, 404])
async def test_polling_waits_until_the_browser_step_is_done(status: int) -> None:
    transport = Script(reply(status))

    result = await ChatgptLogin(transport).poll("dev-1", "AB-CD")  # type: ignore[arg-type]

    assert result is None


async def test_a_finished_login_exchanges_the_code_for_tokens_and_reads_the_account() -> (
    None
):
    access = jwt({"exp": EXPIRES})
    id_token = jwt(
        {
            "email": "owner@example.test",
            AUTH_CLAIM: {"chatgpt_account_id": "acct-9", "chatgpt_plan_type": "pro"},
        }
    )
    transport = Script(
        reply(200, {"authorization_code": "code-1", "code_verifier": "ver-1"}),
        reply(
            200,
            {"access_token": access, "refresh_token": "rt", "id_token": id_token},
        ),
    )

    auth = await ChatgptLogin(transport).poll("dev-1", "AB-CD")  # type: ignore[arg-type]

    assert auth is not None
    assert (
        auth.access_token,
        auth.refresh_token,
        auth.account_id,
        auth.expires_at,
    ) == (
        access,
        "rt",
        "acct-9",
        float(EXPIRES),
    )
    assert transport.calls[0][:2] == ("json", CHATGPT_DEVICE_TOKEN_URL)
    assert transport.calls[1][1] == CHATGPT_OAUTH_TOKEN_URL
    assert dict(transport.calls[1][2]) == {
        "grant_type": "authorization_code",
        "code": "code-1",
        "redirect_uri": DEVICE_REDIRECT_URI,
        "client_id": transport.calls[1][2]["client_id"],
        "code_verifier": "ver-1",
    }


@pytest.mark.parametrize(
    "exchange",
    [
        reply(400, {"error": "invalid_grant"}),
        reply(200, {"access_token": "a", "refresh_token": "r"}),
    ],
)
async def test_an_exchange_without_all_three_tokens_fails(exchange: HttpReply) -> None:
    transport = Script(
        reply(200, {"authorization_code": "c", "code_verifier": "v"}), exchange
    )

    with pytest.raises(ProviderLoginError):
        await ChatgptLogin(transport).poll("dev-1", "AB-CD")  # type: ignore[arg-type]


def test_the_profile_is_read_from_the_id_token() -> None:
    token = jwt({"email": "a@b.test", AUTH_CLAIM: {"chatgpt_plan_type": "plus"}})

    profile = profile_of(token)

    assert (profile.email, profile.plan) == ("a@b.test", "plus")


@pytest.mark.parametrize("token", [None, "", "garbage", "a.b.c"])
def test_an_unreadable_id_token_gives_an_empty_profile(token: str | None) -> None:
    profile = profile_of(token)

    assert (profile.email, profile.plan) == (None, None)


@pytest.mark.parametrize(
    "poll",
    [
        reply(500, "boom"),
        reply(200, {"authorization_code": "c"}),
        reply(200, {"code_verifier": "v"}),
    ],
)
async def test_a_poll_that_fails_or_lacks_the_authorization_code_raises(
    poll: HttpReply,
) -> None:
    with pytest.raises(ProviderLoginError):
        await ChatgptLogin(Script(poll)).poll("dev-1", "AB-CD")  # type: ignore[arg-type]
