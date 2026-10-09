import random
import string

from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.subscriptions.stickiness import (
    SESSION_ID_PARAM,
    prompt_cache_key_of,
    session_id_for,
    sticky_store_key,
    with_session_id,
)


def test_one_chat_key_always_gives_the_same_session() -> None:
    assert len({session_id_for("chat-key") for _ in range(10)}) == 1


def test_property_different_keys_give_different_sessions() -> None:
    rng = random.Random(3)
    keys = {
        "".join(rng.choices(string.ascii_letters, k=rng.randint(1, 40)))
        for _ in range(500)
    }

    assert len({session_id_for(key) for key in keys}) == len(keys)


def test_session_id_looks_like_a_uuid() -> None:
    assert len(session_id_for("k")) == 36 and session_id_for("k").count("-") == 4


def test_store_key_hides_the_prompt_cache_key() -> None:
    assert "secret-chat" not in sticky_store_key("secret-chat")


def test_request_kwargs_gain_the_session_id_without_touching_the_original() -> None:
    kwargs = {"prompt_cache_key": "k1", "model": "m"}

    modified = with_session_id(kwargs)

    assert (modified, SESSION_ID_PARAM in kwargs) == (
        {**kwargs, SESSION_ID_PARAM: session_id_for("k1")},
        False,
    )


def test_requests_without_a_cache_key_keep_the_random_session() -> None:
    assert with_session_id({"model": "m"}) is None


def test_non_string_cache_key_is_ignored() -> None:
    assert (
        prompt_cache_key_of({"prompt_cache_key": 7}),
        prompt_cache_key_of({"prompt_cache_key": ""}),
    ) == (None, None)


def provider_session_header(litellm_params: dict) -> str:  # type: ignore[type-arg]
    config = ChatGPTResponsesAPIConfig()
    headers = config.validate_environment({}, "gpt-x", litellm_params)  # type: ignore[arg-type]
    return headers["session_id"]


AUTH = {
    "access_token": "at",
    "refresh_token": "rt",
    "expires_at": 9_999_999_999,
    "account_id": "acct",
}


def params(**extra: object) -> dict:  # type: ignore[type-arg]
    return {"litellm_credential_name": "cred", "chatgpt_auth": AUTH, **extra}


def test_provider_sends_the_session_derived_from_the_cache_key() -> None:
    modified = with_session_id({"prompt_cache_key": "chat-1"}) or {}

    assert provider_session_header(
        params(litellm_session_id=modified[SESSION_ID_PARAM])
    ) == session_id_for("chat-1")


def test_ten_requests_of_one_chat_carry_one_session() -> None:
    sessions = {
        provider_session_header(
            params(
                **{
                    SESSION_ID_PARAM: (
                        with_session_id({"prompt_cache_key": "chat-1"}) or {}
                    )[SESSION_ID_PARAM]
                }
            )
        )
        for _ in range(10)
    }

    assert len(sessions) == 1


def test_different_chats_carry_different_sessions() -> None:
    first = provider_session_header(params(litellm_session_id=session_id_for("chat-1")))
    second = provider_session_header(
        params(litellm_session_id=session_id_for("chat-2"))
    )

    assert first != second


def test_provider_invents_a_new_session_per_request_without_the_plugin() -> None:
    assert provider_session_header(params()) != provider_session_header(params())
