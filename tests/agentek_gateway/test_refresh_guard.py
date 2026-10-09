import threading
import time

import fakeredis
import pytest
from litellm.llms.chatgpt import authenticator
from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

from agentek_gateway.subscriptions.providers.chatgpt import ChatgptAuth
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.refresh_guard import (
    RefreshGuard,
    install_refresh_guard,
    uninstall_refresh_guard,
)
from agentek_gateway.subscriptions.token_coordination import (
    LatestAuth,
    SyncTokenCoordinator,
)

CREDENTIAL = "cred-a"
REFRESH_DURATION_S = 0.15
EXPIRED_AT = 1.0


class CountingAuthenticator(Authenticator):
    """Authenticator whose network refresh is replaced by a counted, slow stand-in."""

    calls: list[str] = []  # noqa: RUF012

    def _request_refreshed_tokens(self, refresh_token: str) -> dict[str, str]:
        type(self).calls.append(refresh_token)
        time.sleep(REFRESH_DURATION_S)
        number = len(type(self).calls)
        return {
            "access_token": f"at-new{number}",
            "refresh_token": f"rt-new{number}",
            "id_token": f"id-new{number}",
        }


def expired_authenticator() -> CountingAuthenticator:
    return CountingAuthenticator(
        auth_inline={
            "access_token": "at-old",
            "refresh_token": "rt-old",
            "expires_at": EXPIRED_AT,
        },
        credential_required=True,
        credential_name=CREDENTIAL,
    )


@pytest.fixture
def coordinator():  # type: ignore[no-untyped-def]
    CountingAuthenticator.calls = []
    redis = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    shared = SyncTokenCoordinator(redis, Keys("t:"), lock_wait_s=2.0)
    yield shared
    uninstall_refresh_guard()


def refresh_in_threads(count: int) -> list[str]:
    results: list[str] = []

    def work() -> None:
        results.append(expired_authenticator().get_access_token())

    threads = [threading.Thread(target=work) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_without_the_guard_concurrent_requests_each_refresh(coordinator) -> None:  # type: ignore[no-untyped-def]
    refresh_in_threads(2)

    assert len(CountingAuthenticator.calls) == 2


def test_guard_lets_exactly_one_of_two_concurrent_refreshes_reach_the_provider(coordinator) -> None:  # type: ignore[no-untyped-def]
    install_refresh_guard(RefreshGuard(coordinator))

    results = refresh_in_threads(2)

    assert (len(CountingAuthenticator.calls), len(set(results))) == (1, 1)


def test_guard_publishes_the_new_pair_before_it_is_persisted(coordinator) -> None:  # type: ignore[no-untyped-def]
    install_refresh_guard(RefreshGuard(coordinator))

    expired_authenticator().get_access_token()

    latest = coordinator.read_latest(CREDENTIAL)
    assert latest.auth.refresh_token == "rt-new1"  # type: ignore[union-attr]


def test_guard_marks_the_refresh_so_that_a_following_401_is_forgiven(coordinator) -> None:  # type: ignore[no-untyped-def]
    install_refresh_guard(RefreshGuard(coordinator))

    expired_authenticator().get_access_token()

    assert (
        coordinator._redis.exists(Keys("t:").refreshed(CREDENTIAL)) == 1
    )  # noqa: SLF001


def test_newer_pair_from_another_replica_is_used_without_calling_the_provider(coordinator) -> None:  # type: ignore[no-untyped-def]
    coordinator.save_latest(
        CREDENTIAL,
        LatestAuth(ChatgptAuth("at-peer", "rt-peer", id_token="id-peer")),
    )
    install_refresh_guard(RefreshGuard(coordinator))

    token = expired_authenticator().get_access_token()

    assert (token, CountingAuthenticator.calls) == ("at-peer", [])


def test_pair_equal_to_the_stale_one_is_not_treated_as_newer(coordinator) -> None:  # type: ignore[no-untyped-def]
    coordinator.save_latest(CREDENTIAL, LatestAuth(ChatgptAuth("at-old", "rt-old")))
    install_refresh_guard(RefreshGuard(coordinator))

    expired_authenticator().get_access_token()

    assert CountingAuthenticator.calls == ["rt-old"]


def test_unobtainable_lock_fails_the_refresh_instead_of_calling_the_provider(coordinator) -> None:  # type: ignore[no-untyped-def]
    holder = coordinator.acquire(CREDENTIAL)
    assert holder is not None
    install_refresh_guard(
        RefreshGuard(
            SyncTokenCoordinator(coordinator._redis, Keys("t:"), lock_wait_s=0.1)
        )
    )  # noqa: SLF001

    with pytest.raises(RefreshAccessTokenError):
        expired_authenticator()._refresh_tokens("rt-old")  # noqa: SLF001

    assert CountingAuthenticator.calls == []


def test_authenticator_exposes_the_hook_the_plugin_installs() -> None:
    assert hasattr(authenticator, "REFRESH_GUARD")


def test_waiting_for_a_lock_held_in_the_same_loop_ends_quickly(coordinator) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    async def scenario() -> float:
        holder = coordinator.acquire(CREDENTIAL)
        assert holder is not None
        install_refresh_guard(
            RefreshGuard(
                SyncTokenCoordinator(coordinator._redis, Keys("t:"))
            )  # noqa: SLF001
        )
        started = time.monotonic()
        with pytest.raises(RefreshAccessTokenError):
            expired_authenticator()._refresh_tokens("rt-old")  # noqa: SLF001
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 3.0
