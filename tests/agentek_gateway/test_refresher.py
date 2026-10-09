import asyncio

import fakeredis

import pytest

from agentek_gateway.subscriptions.events import Unauthorized
from agentek_gateway.subscriptions.model import SubscriptionState as S
from agentek_gateway.subscriptions.providers.base import (
    RefreshedTokens,
    RefreshRejected,
)
from agentek_gateway.subscriptions.providers.chatgpt import ChatgptAuth
from agentek_gateway.subscriptions.refresh_guard import RefreshGuard
from agentek_gateway.subscriptions.token_coordination import (
    LatestAuth,
    SyncTokenCoordinator,
)

from .upkeep import HOUR_S, build_upkeep, tokens

CRED = "cred-a"
SAVED_PAIR_TTL_S = 60


async def state_of(replica, sub_id: str = "a"):  # type: ignore[no-untyped-def]
    record = await replica.store.read_state(sub_id)
    return record.state if record else None


async def test_token_close_to_expiry_is_refreshed_and_saved() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=300)
    replica = upkeep.replica()

    await replica.refresher.tick()

    stored = await upkeep.credentials.read_auth(CRED)
    assert (upkeep.provider.refresh_tokens, stored.auth.access_token, stored.auth.account_id) == (  # type: ignore[union-attr]
        ["rt-0"],
        "at-new1",
        "acct",
    )


async def test_token_with_enough_lifetime_left_is_left_alone() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=HOUR_S)

    await upkeep.replica().refresher.tick()

    assert upkeep.provider.refresh_tokens == []


async def test_disabled_subscription_is_not_refreshed() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    await upkeep.repo.set_enabled("a", False)

    await upkeep.replica().refresher.tick()

    assert upkeep.provider.refresh_tokens == []


async def test_two_replicas_refreshing_at_once_call_the_provider_once() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    first, second = upkeep.replica(), upkeep.replica()

    await asyncio.gather(first.refresher.tick(), second.refresher.tick())

    stored = await upkeep.credentials.read_auth(CRED)
    assert (len(upkeep.provider.refresh_tokens), stored.auth.access_token) == (1, "at-new1")  # type: ignore[union-attr]


async def test_second_replica_after_the_first_does_not_refresh_again() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    await upkeep.replica().refresher.tick()

    await upkeep.replica().refresher.tick()

    assert len(upkeep.provider.refresh_tokens) == 1


async def test_crash_after_the_provider_answered_loses_no_token() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    upkeep.credentials.failures_left = 1
    crashed = upkeep.replica()

    await crashed.refresher.tick()
    lost = await upkeep.credentials.read_auth(CRED)
    restarted = upkeep.replica()
    await restarted.refresher.tick()
    saved = await upkeep.credentials.read_auth(CRED)

    assert (
        lost.auth.access_token,  # type: ignore[union-attr]
        saved.auth.access_token,  # type: ignore[union-attr]
        len(upkeep.provider.refresh_tokens),
        await state_of(restarted),
    ) == ("at-0", "at-new1", 1, None)


async def test_pair_saved_by_a_mid_request_refresh_is_written_to_the_database() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=HOUR_S)
    replica = upkeep.replica()
    await replica.coordinator.save_latest(
        CRED, LatestAuth(tokens(HOUR_S, upkeep.clock.now(), "mid"))
    )

    await replica.refresher.tick()

    stored = await upkeep.credentials.read_auth(CRED)
    latest = await replica.coordinator.read_latest(CRED)
    assert (
        stored.auth.refresh_token,
        latest.auth.refresh_token,  # type: ignore[union-attr]
        latest.persisted,  # type: ignore[union-attr]
        upkeep.provider.refresh_tokens,
    ) == ("rt-mid", "rt-mid", True, [])


async def test_pair_older_than_a_reauthorization_is_dropped_not_written_over_it() -> (
    None
):
    upkeep, _ = build_upkeep(["a"], expires_in_s=HOUR_S)
    replica = upkeep.replica()
    await replica.coordinator.save_latest(
        CRED, LatestAuth(tokens(HOUR_S, upkeep.clock.now(), "old"))
    )
    stored = await upkeep.credentials.read_auth(CRED)
    upkeep.credentials.put(CRED, tokens(HOUR_S, upkeep.clock.now(), "reauth"))
    original_read = upkeep.credentials.read_auth

    async def stale_read(name: str):  # type: ignore[no-untyped-def]
        upkeep.credentials.read_auth = original_read  # type: ignore[method-assign]
        return stored

    upkeep.credentials.read_auth = stale_read  # type: ignore[method-assign]

    await replica.refresher.tick()

    after = await upkeep.credentials.read_auth(CRED)
    assert (after.auth.refresh_token, await replica.coordinator.read_latest(CRED)) == (  # type: ignore[union-attr]
        "rt-reauth",
        None,
    )


async def test_refresh_leaves_a_mark_that_tells_a_following_401_apart() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    replica = upkeep.replica()
    await replica.refresher.tick()

    assert await replica.store.recently_refreshed(CRED)


async def test_authentication_refreshing_state_forces_a_refresh_and_goes_to_probe() -> (
    None
):
    upkeep, subscriptions = build_upkeep(["a"], expires_in_s=HOUR_S)
    replica = upkeep.replica()
    await replica.states.apply(subscriptions[0], Unauthorized())

    await replica.refresher.tick()

    assert (len(upkeep.provider.refresh_tokens), await state_of(replica)) == (
        1,
        S.HALF_OPEN,
    )


async def test_revoked_refresh_token_needs_a_person() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    upkeep.provider.outcomes.append(RefreshRejected(permanent=True))
    replica = upkeep.replica()

    await replica.refresher.tick()

    assert await state_of(replica) == S.AUTH_FAILED


async def test_temporary_refresh_failure_changes_nothing_and_is_retried() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    upkeep.provider.outcomes.append(RefreshRejected(permanent=False))
    replica = upkeep.replica()

    await replica.refresher.tick()
    first_state = await state_of(replica)
    await replica.refresher.tick()

    assert (first_state, len(upkeep.provider.refresh_tokens)) == (None, 2)


async def test_subscription_without_tokens_is_skipped() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    del upkeep.credentials.values[CRED]

    await upkeep.replica().refresher.tick()

    assert upkeep.provider.refresh_tokens == []


async def test_failure_of_one_subscription_does_not_stop_the_others() -> None:
    upkeep, _ = build_upkeep(["a", "b"], expires_in_s=60)
    upkeep.credentials.failures_left = 1

    await upkeep.replica().refresher.tick()

    assert len(upkeep.provider.refresh_tokens) == 2
    _ = pytest, RefreshedTokens


async def test_refresh_keeps_the_account_and_identity_token_when_the_provider_omits_them() -> (
    None
):
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    replica = upkeep.replica()

    await replica.refresher.tick()

    stored = await upkeep.credentials.read_auth(CRED)
    assert (stored.auth.account_id, stored.auth.id_token) == ("acct", "id-0")  # type: ignore[union-attr]


async def test_refresh_is_postponed_while_a_newer_pair_waits_to_be_saved() -> None:
    upkeep, subscriptions = build_upkeep(["a"], expires_in_s=60)
    replica = upkeep.replica()
    await replica.coordinator.save_latest(
        CRED, LatestAuth(tokens(HOUR_S, upkeep.clock.now(), "mid"))
    )

    await replica.refresher.refresh(subscriptions[0])

    assert upkeep.provider.refresh_tokens == []


async def test_pair_written_to_the_database_stays_briefly_for_replicas_holding_the_old_one() -> (
    None
):
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    replica = upkeep.replica()

    await replica.refresher.tick()

    latest = await replica.coordinator.read_latest(CRED)
    ttl = await replica.redis.ttl(upkeep.keys.latest_auth(CRED))
    assert (latest.auth.access_token, latest.persisted, 0 < ttl <= SAVED_PAIR_TTL_S) == (  # type: ignore[union-attr]
        "at-new1",
        True,
        True,
    )


async def test_pair_the_database_has_not_accepted_is_kept_for_a_day() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    upkeep.credentials.failures_left = 1
    replica = upkeep.replica()

    await replica.refresher.tick()

    ttl = await replica.redis.ttl(upkeep.keys.latest_auth(CRED))
    assert ttl > HOUR_S


async def test_replica_with_the_old_pair_gets_the_new_one_from_redis_not_from_the_provider() -> (
    None
):
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    await upkeep.replica().refresher.tick()
    guard_calls: list[str] = []
    guard = RefreshGuard(
        SyncTokenCoordinator(
            fakeredis.FakeRedis(server=upkeep.server, decode_responses=True),
            upkeep.keys,
        )
    )

    def refresh_with_the_provider() -> dict[str, str]:
        guard_calls.append("provider")
        return {}

    pair = guard(CRED, "rt-0", refresh_with_the_provider)

    assert (pair["refresh_token"], guard_calls) == ("rt-new1", [])


async def test_pair_saved_before_a_reauthorization_is_dropped_from_redis() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=HOUR_S)
    replica = upkeep.replica()
    await replica.coordinator.save_latest(
        CRED, LatestAuth(tokens(HOUR_S, upkeep.clock.now(), "saved"), True)
    )
    upkeep.credentials.put(CRED, tokens(HOUR_S, upkeep.clock.now(), "reauth"))

    await replica.refresher.tick()

    stored = await upkeep.credentials.read_auth(CRED)
    assert (stored.auth.refresh_token, await replica.coordinator.read_latest(CRED)) == (  # type: ignore[union-attr]
        "rt-reauth",
        None,
    )


async def test_pair_the_database_refused_stays_in_the_shared_store_for_the_next_pass() -> (
    None
):
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    upkeep.credentials.failures_left = 1
    replica = upkeep.replica()

    await replica.refresher.tick()

    assert (await replica.coordinator.read_latest(CRED)).auth.access_token == "at-new1"  # type: ignore[union-attr]


async def test_refresh_that_outlasts_its_deadline_is_abandoned_and_frees_the_lock() -> (
    None
):
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    upkeep.provider.delay_s = 3.0
    replica = upkeep.replica(deadline_s=0.05)

    await replica.refresher.tick()

    stored = await upkeep.credentials.read_auth(CRED)
    assert (
        await replica.redis.exists(upkeep.keys.refresh_lock(CRED)),
        stored.auth.access_token,  # type: ignore[union-attr]
    ) == (0, "at-0")


async def test_replica_that_lost_the_lease_refreshes_nothing() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)

    async def lost() -> bool:
        return False

    await upkeep.replica(still_leader=lost).refresher.tick()

    assert upkeep.provider.refresh_tokens == []


async def test_lease_is_checked_before_every_subscription() -> None:
    upkeep, _ = build_upkeep(["a", "b", "c"], expires_in_s=HOUR_S)
    checks = []

    async def counting() -> bool:
        checks.append(1)
        return True

    await upkeep.replica(still_leader=counting).refresher.tick()

    assert len(checks) == 3


async def test_new_access_token_without_a_new_refresh_token_is_written_to_the_database() -> (
    None
):
    upkeep, _ = build_upkeep(["a"], expires_in_s=HOUR_S)
    replica = upkeep.replica()
    same_refresh = tokens(HOUR_S, upkeep.clock.now(), "0")
    await replica.coordinator.save_latest(
        CRED,
        LatestAuth(
            ChatgptAuth(
                "at-fresh",
                same_refresh.refresh_token,
                expires_at=same_refresh.expires_at,
            )
        ),
    )

    await replica.refresher.tick()

    stored = await upkeep.credentials.read_auth(CRED)
    assert stored.auth.access_token == "at-fresh"  # type: ignore[union-attr]
