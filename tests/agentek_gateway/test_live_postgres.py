"""The Prisma-backed repositories against a real Postgres with the real generated client."""

import asyncio
from datetime import datetime, timezone

import pytest

from agentek_gateway.subscriptions.credentials import PrismaCredentialStore
from agentek_gateway.subscriptions.model import (
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState as S,
)
from agentek_gateway.subscriptions.policy import (
    Subject,
    SubjectKind,
    Visibility,
    VisibilityKind,
)
from agentek_gateway.subscriptions.prisma_repos import (
    PrismaPolicyRepo,
    PrismaSubscriptionRepo,
)
from agentek_gateway.subscriptions.providers.chatgpt import ChatgptAuth
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.state_db import PrismaStateDb

from .conftest import FakeClock
from .live import live_db, live_redis, needs_postgres, needs_redis

pytestmark = needs_postgres


def record(
    state: S, version: int, until: float | None = None, streak: int = 0
) -> StateRecord:
    return StateRecord(
        state,
        version,
        1_000_000.0,
        until,
        StateReason.LIMIT_EXHAUSTED,
        SignalSource.PROVIDER_RESPONSE,
        streak,
    )


async def add_subscription(db, sub_id: str, **fields: object) -> None:  # type: ignore[no-untyped-def]
    data = {
        "id": sub_id,
        "provider": "chatgpt",
        "name": f"name-{sub_id}",
        "credential_name": f"cred-{sub_id}",
        **fields,
    }
    await db.litellm_agenteksubscription.create(data=data)


async def test_subscriptions_are_listed_with_every_field_mapped() -> None:
    async with live_db() as db:
        await add_subscription(
            db, "a", priority=10, enabled=False, max_concurrency=4, egress="eu"
        )
        await add_subscription(db, "b")
        repo = PrismaSubscriptionRepo(lambda: db.litellm_agenteksubscription)

        listed = {sub.id: sub for sub in await repo.list_subscriptions()}

        assert (
            listed["a"].priority,
            listed["a"].enabled,
            listed["a"].concurrency_limit,
            listed["a"].egress,
        ) == (
            10,
            False,
            4,
            "eu",
        )
        assert (
            listed["b"].priority,
            listed["b"].enabled,
            listed["b"].concurrency_limit,
            listed["b"].egress,
        ) == (
            50,
            True,
            None,
            None,
        )
        assert (
            listed["b"].name,
            listed["b"].credential_name,
            listed["b"].provider,
        ) == (
            "name-b",
            "cred-b",
            "chatgpt",
        )


async def test_switch_is_written_to_the_subscription_record() -> None:
    async with live_db() as db:
        await add_subscription(db, "a")
        repo = PrismaSubscriptionRepo(lambda: db.litellm_agenteksubscription)

        await repo.set_enabled("a", False)
        off = (await repo.list_subscriptions())[0].enabled
        await repo.set_enabled("a", True)
        on = (await repo.list_subscriptions())[0].enabled

        assert (off, on) == (False, True)


async def test_policy_rows_become_visibility_bindings_and_a_version() -> None:
    async with live_db() as db:
        for sub_id in ("a", "b", "c"):
            await add_subscription(db, sub_id)
        table = db.litellm_agenteksubscriptionpolicy
        await table.create(data={"subscription_id": "a", "version": 3})
        await table.create(
            data={
                "subscription_id": "b",
                "visibility": "only",
                "visibility_subjects": ["employee:7", "bogus:1", "space:9"],
                "bound_subjects": ["space:9"],
                "version": 5,
            }
        )
        await table.create(
            data={"subscription_id": "c", "visibility": "something_new", "version": 1}
        )
        repo = PrismaPolicyRepo(lambda: table)

        policy = await repo.load_policy()

        assert policy.version == 5
        assert policy.visibility["a"] == Visibility(VisibilityKind.ALL, frozenset())
        assert policy.visibility["b"] == Visibility(
            VisibilityKind.ONLY,
            frozenset(
                {Subject(SubjectKind.EMPLOYEE, "7"), Subject(SubjectKind.SPACE, "9")}
            ),
        )
        assert policy.visibility["c"] == Visibility(VisibilityKind.ONLY, frozenset())
        assert policy.bindings == {"b": frozenset({Subject(SubjectKind.SPACE, "9")})}


async def test_empty_policy_table_gives_the_default_policy() -> None:
    async with live_db() as db:
        repo = PrismaPolicyRepo(lambda: db.litellm_agenteksubscriptionpolicy)

        policy = await repo.load_policy()

        assert (policy.visibility, policy.bindings, policy.version) == ({}, {}, 0)


async def test_state_is_written_read_back_and_a_stale_write_is_ignored() -> None:
    async with live_db() as db:
        await add_subscription(db, "a")
        states = PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate)
        until = 1_900_000_000.0

        await states.write_state("a", record(S.RATE_LIMITED, 2, until, streak=2))
        await states.write_state("a", record(S.BANNED, 1))
        read = await states.read_state("a")

        assert read is not None
        assert (read.state, read.version, read.overload_streak) == (
            S.RATE_LIMITED,
            2,
            2,
        )
        assert read.until == pytest.approx(until, abs=0.01)
        assert read.reason is StateReason.LIMIT_EXHAUSTED


async def test_newer_write_replaces_and_delete_respects_versions() -> None:
    async with live_db() as db:
        await add_subscription(db, "a")
        states = PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate)
        await states.write_state("a", record(S.BANNED, 3))

        await states.write_state("a", record(S.AUTH_FAILED, 4))
        await states.delete_state("a", 3)
        kept = await states.read_state("a")
        await states.delete_state("a", 4)

        assert (kept.state if kept else None, await states.read_state("a")) == (
            S.AUTH_FAILED,
            None,
        )


async def test_writers_racing_in_any_order_leave_the_newest_state() -> None:
    async with live_db() as db:
        await add_subscription(db, "a")
        states = PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate)

        for round_number in range(5):
            base = round_number * 10
            await asyncio.gather(
                *(
                    states.write_state(
                        "a", record(S.OVERLOADED, base + version, 1_900_000_000.0)
                    )
                    for version in (3, 1, 2, 5, 4)
                )
            )

        assert (await states.read_state("a")).version == 45  # type: ignore[union-attr]


async def test_batch_read_by_ids() -> None:
    async with live_db() as db:
        for sub_id in ("a", "b", "c", "d"):
            await add_subscription(db, sub_id)
        states = PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate)
        for sub_id in ("a", "c", "d"):
            await states.write_state(sub_id, record(S.BANNED, 1))

        rows = await states.read_states(["a", "b", "c", "zzz"])

        assert (sorted(rows), sorted(await states.read_all_states())) == (
            ["a", "c"],
            ["a", "c", "d"],
        )


async def test_state_of_an_unknown_subscription_is_refused_by_the_foreign_key() -> None:
    async with live_db() as db:
        states = PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate)

        with pytest.raises(Exception):  # noqa: B017, PT011
            await states.write_state("missing", record(S.BANNED, 1))


async def test_deleting_a_subscription_removes_its_state_row() -> None:
    async with live_db() as db:
        await add_subscription(db, "a")
        states = PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate)
        await states.write_state("a", record(S.BANNED, 1))

        await db.litellm_agenteksubscription.delete(where={"id": "a"})

        assert await states.read_all_states() == {}


CREDENTIAL_VALUES = (
    '{"chatgpt_api_base": "http://x", "chatgpt_auth": {"access_token": "at-old", '
    '"refresh_token": "rt-old", "id_token": "id-old", "expires_at": 1900000000, "account_id": "acct"}}'
)


async def add_credential(db) -> None:  # type: ignore[no-untyped-def]
    await db.litellm_credentialstable.create(
        data={
            "credential_name": "cred-a",
            "credential_values": CREDENTIAL_VALUES,
            "created_by": "test",
            "updated_by": "test",
        }
    )


async def test_tokens_are_read_from_the_credentials_table() -> None:
    async with live_db() as db:
        await add_credential(db)
        store = PrismaCredentialStore(lambda: db.litellm_credentialstable)

        stored = await store.read_auth("cred-a")

        assert stored is not None
        assert (
            stored.auth.refresh_token,
            stored.auth.account_id,
            stored.auth.expires_at,
        ) == (
            "rt-old",
            "acct",
            1_900_000_000.0,
        )


async def test_tokens_of_many_credentials_are_read_with_one_query() -> None:
    async with live_db() as db:
        await add_credential(db)
        store = PrismaCredentialStore(lambda: db.litellm_credentialstable)

        found = await store.read_auths(["cred-a", "cred-missing"])

        assert {name: stored.auth.refresh_token for name, stored in found.items()} == {
            "cred-a": "rt-old"
        }


async def test_token_write_keeps_the_other_credential_values_and_bumps_the_version() -> (
    None
):
    async with live_db() as db:
        await add_credential(db)
        store = PrismaCredentialStore(lambda: db.litellm_credentialstable)
        before = await store.read_auth("cred-a")

        written = await store.write_auth_if_unchanged(
            "cred-a", before, ChatgptAuth("at-new", "rt-new", expires_at=1_950_000_000.0)  # type: ignore[arg-type]
        )

        after = await store.read_auth("cred-a")
        assert written is True
        assert (after.auth.access_token, after.auth.account_id, after.auth.id_token) == (  # type: ignore[union-attr]
            "at-new",
            "acct",
            "id-old",
        )
        assert after.values["chatgpt_api_base"] == "http://x"  # type: ignore[union-attr]
        assert after.version != before.version  # type: ignore[union-attr]


async def test_second_token_write_with_the_old_version_is_refused() -> None:
    async with live_db() as db:
        await add_credential(db)
        store = PrismaCredentialStore(lambda: db.litellm_credentialstable)
        before = await store.read_auth("cred-a")

        results = await asyncio.gather(
            *(
                store.write_auth_if_unchanged("cred-a", before, ChatgptAuth(f"at-{n}", f"rt-{n}"))  # type: ignore[arg-type]
                for n in range(4)
            )
        )

        assert results.count(True) == 1


async def test_write_after_an_outside_edit_of_the_credential_is_refused() -> None:
    async with live_db() as db:
        await add_credential(db)
        store = PrismaCredentialStore(lambda: db.litellm_credentialstable)
        before = await store.read_auth("cred-a")
        await db.litellm_credentialstable.update(
            where={"credential_name": "cred-a"}, data={"updated_by": "operator"}
        )

        written = await store.write_auth_if_unchanged(
            "cred-a", before, ChatgptAuth("at-new", "rt-new")  # type: ignore[arg-type]
        )

        assert written is False


@needs_redis
async def test_state_survives_flushall_through_the_real_database() -> None:
    async with live_db() as db, live_redis() as redis:
        await add_subscription(db, "a")
        clock = FakeClock(start=datetime.now(timezone.utc).timestamp())
        store = RedisStateStore(
            redis,
            PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate),
            clock,
            Keys("live:"),
        )
        until = clock.now() + 3600
        await store.compare_and_set_state("a", None, record(S.RATE_LIMITED, 1, until))
        await redis.flushall()

        states = await store.read_states(["a"])

        assert states["a"].state is S.RATE_LIMITED


@needs_redis
async def test_toggle_reaches_the_database_the_flag_and_the_state() -> None:
    from agentek_gateway.subscriptions.config import GatewayConfig
    from agentek_gateway.subscriptions.service import StateService
    from agentek_gateway.subscriptions.toggle import SubscriptionToggle

    async with live_db() as db, live_redis() as redis:
        await add_subscription(db, "a")
        clock = FakeClock(start=datetime.now(timezone.utc).timestamp())
        store = RedisStateStore(
            redis,
            PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate),
            clock,
            Keys("live:"),
        )
        repo = PrismaSubscriptionRepo(lambda: db.litellm_agenteksubscription)
        subscription = (await repo.list_subscriptions())[0]
        toggle = SubscriptionToggle(
            store, repo, StateService(store, clock, GatewayConfig())
        )

        await toggle.set_enabled(subscription, False)

        row = await db.litellm_agenteksubscription.find_unique(where={"id": "a"})
        state_row = await db.litellm_agenteksubscriptionstate.find_unique(
            where={"subscription_id": "a"}
        )
        assert (row.enabled, state_row.state, await store.read_enabled_flags()) == (  # type: ignore[union-attr]
            False,
            "DISABLED",
            {"a": False},
        )


class DatabaseDown(PrismaStateDb):
    """Refuses writes while ``down`` is set, like a database that went away between two state changes."""

    down = True

    async def write_state(self, subscription_id, record):  # type: ignore[no-untyped-def]
        if self.down:
            raise ConnectionError("database down")
        await super().write_state(subscription_id, record)


@needs_redis
async def test_reconcile_writes_the_state_the_database_missed_into_a_real_row() -> None:
    async with live_db() as db, live_redis() as redis:
        await add_subscription(db, "a")
        clock = FakeClock(start=datetime.now(timezone.utc).timestamp())
        state_db = DatabaseDown(lambda: db.litellm_agenteksubscriptionstate)
        store = RedisStateStore(redis, state_db, clock, Keys("live:"))
        await store.compare_and_set_state("a", None, record(S.BANNED, 1))
        rows_while_down = await db.litellm_agenteksubscriptionstate.count()
        state_db.down = False

        await store.reconcile(["a"])

        row = await db.litellm_agenteksubscriptionstate.find_unique(
            where={"subscription_id": "a"}
        )
        assert (rows_while_down, row.state if row else None) == (0, "BANNED")


@needs_redis
async def test_reconcile_restores_a_durable_state_from_the_database_after_flushall() -> (
    None
):
    async with live_db() as db, live_redis() as redis:
        await add_subscription(db, "a")
        clock = FakeClock(start=datetime.now(timezone.utc).timestamp())
        state_db = DatabaseDown(lambda: db.litellm_agenteksubscriptionstate)
        state_db.down = False
        store = RedisStateStore(redis, state_db, clock, Keys("live:"))
        await store.compare_and_set_state("a", None, record(S.BROKEN, 1))
        await redis.flushall()

        await store.reconcile(["a"])

        assert (await redis.get(Keys("live:").state("a"))) is not None
