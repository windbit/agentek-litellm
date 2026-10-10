import pytest
from redis.asyncio import Redis

from agentek_gateway.subscriptions.plugin import redis_url_from_env

AWKWARD_PASSWORDS = ["a/b@c:d#e", "p%40ss", "x y?z&w=1", "plain"]


@pytest.mark.parametrize("password", AWKWARD_PASSWORDS)
def test_password_from_its_own_variable_survives_url_parsing(password: str) -> None:
    url = redis_url_from_env(
        {
            "REDIS_HOST": "redis.internal",
            "REDIS_PORT": "6380",
            "REDIS_DB": "2",
            "REDIS_PASSWORD": password,
        }
    )

    kwargs = Redis.from_url(url).connection_pool.connection_kwargs

    assert (kwargs["host"], kwargs["port"], kwargs["db"], kwargs["password"]) == (
        "redis.internal",
        6380,
        2,
        password,
    )


def test_missing_variables_give_the_local_default() -> None:
    kwargs = Redis.from_url(redis_url_from_env({})).connection_pool.connection_kwargs

    assert (kwargs["host"], kwargs["port"], kwargs["db"], kwargs.get("password")) == (
        "localhost",
        6379,
        0,
        None,
    )


def test_explicit_url_is_used_as_given() -> None:
    url = redis_url_from_env(
        {"AGENTEK_GATEWAY_REDIS_URL": "redis://:x@other:1/5", "REDIS_HOST": "ignored"}
    )

    assert url == "redis://:x@other:1/5"
