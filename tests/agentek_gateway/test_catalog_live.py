"""The catalog writers against a real Postgres with the real generated client."""

import json
from types import SimpleNamespace

import fakeredis
import pytest

from agentek_gateway.subscriptions.slots import InMemorySlotStore
from agentek_gateway.subscriptions.stats_report import EmptyStats
from agentek_gateway.subscriptions.admin import (
    AdminDeps,
    NewSubscriptionTarget,
    ReauthorizeTarget,
    SubscriptionAdmin,
)
from agentek_gateway.subscriptions.audit import AuditEntry, PrismaAuditLog
from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.credential_directory import (
    PrismaCredentialDirectory,
)
from agentek_gateway.subscriptions.credentials import PrismaCredentialStore
from agentek_gateway.subscriptions.litellm_deployments import PrismaModelStore
from agentek_gateway.subscriptions.model_copies import CopySync
from agentek_gateway.subscriptions.prisma_repos import (
    NewSubscription,
    PrismaSubscriptionRepo,
    PrismaSubscriptionWriter,
)
from agentek_gateway.subscriptions.provider_settings import PrismaProviderSettingsRepo
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.service import StateService
from agentek_gateway.subscriptions.state_db import PrismaStateDb
from agentek_gateway.subscriptions.toggle import SubscriptionToggle
from agentek_gateway.subscriptions.token_coordination import TokenCoordinator

from agentek_gateway.subscriptions.unit import PrismaUnit, Writes

from .admin_stack import RecordingRuntime, ScriptedLogin, ScriptedUsage
from .catalog_stack import PROVIDER, auth_of
from .conftest import FakeClock
from .live import live_db, needs_postgres

pytestmark = needs_postgres


async def test_a_subscription_is_created_once_per_name_and_updated_and_deleted_with_its_rows() -> (
    None
):
    async with live_db() as db:
        writer = PrismaSubscriptionWriter(lambda: db.litellm_agenteksubscription)

        first = await writer.create_subscription(NewSubscription("chatgpt", "n", "c"))
        again = await writer.create_subscription(NewSubscription("chatgpt", "n", "c2"))
        assert first is not None and again is None
        await writer.update_subscription(
            first.id, {"priority": 5, "max_concurrency": 2}
        )
        await db.litellm_agenteksubscriptionstate.create(
            data={"subscription_id": first.id, "state": "BANNED"}
        )
        await db.litellm_agenteksubscriptionpolicy.create(
            data={"subscription_id": first.id}
        )
        row = await db.litellm_agenteksubscription.find_unique(where={"id": first.id})
        assert (row.priority, row.max_concurrency, row.credential_name) == (5, 2, "c")  # type: ignore[union-attr]

        await writer.delete_subscription(first.id)

        assert (
            await db.litellm_agenteksubscription.count(),
            await db.litellm_agenteksubscriptionstate.count(),
            await db.litellm_agenteksubscriptionpolicy.count(),
        ) == (0, 0, 0)


async def test_a_credential_is_created_replaced_without_losing_other_values_and_deleted() -> (
    None
):
    async with live_db() as db:
        directory = PrismaCredentialDirectory(lambda: db.litellm_credentialstable)
        store = PrismaCredentialStore(lambda: db.litellm_credentialstable)

        created = await directory.create_credential("c", PROVIDER, auth_of("1"))
        duplicate = await directory.create_credential("c", PROVIDER, auth_of("2"))
        row = await db.litellm_credentialstable.find_unique(
            where={"credential_name": "c"}
        )
        await db.litellm_credentialstable.update(
            where={"credential_name": "c"},
            data={
                "credential_values": json.dumps(
                    {**row.credential_values, "extra": "kept"}  # type: ignore[union-attr,arg-type,misc]
                )
            },
        )
        await directory.replace_auth("c", auth_of("3"))
        values = (
            await db.litellm_credentialstable.find_unique(
                where={"credential_name": "c"}
            )
        ).credential_values  # type: ignore[union-attr]
        listed = await directory.list_credentials(PROVIDER)
        stored = await store.read_auth("c")

        await directory.delete_credential("c")

        assert (created, duplicate) == (True, False)
        assert (values["extra"], values["chatgpt_auth"]["access_token"]) == (
            "kept",
            "access-secret-3",
        )
        assert [(item.name, item.has_tokens, item.disabled) for item in listed] == [
            ("c", True, False)
        ]
        assert stored.auth.access_token == "access-secret-3"  # type: ignore[union-attr]
        assert await directory.list_credentials(PROVIDER) == ()


async def test_provider_settings_are_upserted_and_loaded_with_validation() -> None:
    async with live_db() as db:
        settings = PrismaProviderSettingsRepo(lambda: db.litellm_config)

        await settings.set_concurrency("chatgpt", 3)
        await settings.set_concurrency("chatgpt", 4)
        await settings.set_concurrency("other", None)
        await db.litellm_config.create(
            data={
                "param_name": "agentek_provider:broken",
                "param_value": json.dumps({"concurrency_limit": -1}),
            }
        )
        await db.litellm_config.create(
            data={
                "param_name": "unrelated",
                "param_value": json.dumps({"concurrency_limit": 9}),
            }
        )

        loaded = await settings.load()

        assert {name: item.concurrency_limit for name, item in loaded.items()} == {
            "chatgpt": 4,
            "other": None,
            "broken": None,
        }


async def test_an_audit_entry_keeps_who_what_and_the_changed_values_as_json() -> None:
    async with live_db() as db:
        audit = PrismaAuditLog(lambda: db.litellm_agentekaudit)

        await audit.record(
            AuditEntry(
                "op",
                "subscription.settings",
                "id-1",
                "n",
                None,
                {"priority": 1},
                {"priority": 2},
            )
        )
        await audit.record(
            AuditEntry("op", "subscription.provider_concurrency", subject="chatgpt")
        )

        rows = await db.litellm_agentekaudit.find_many(order={"created_at": "asc"})
        assert [
            (row.actor, row.action, row.subscription_name, row.subject) for row in rows
        ] == [
            ("op", "subscription.settings", "n", None),
            ("op", "subscription.provider_concurrency", None, "chatgpt"),
        ]
        assert (rows[0].before, rows[0].after, rows[1].before) == (
            {"priority": 1},
            {"priority": 2},
            None,
        )


async def test_an_operator_can_sign_in_reauthorize_and_remove_a_subscription_on_a_real_database() -> (
    None
):
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.management_endpoints.model_management_endpoints import (
        _add_model_to_db,
    )
    from litellm.types.router import Deployment, LiteLLM_Params, ModelInfo

    async with live_db() as db:
        clock, redis = FakeClock(), fakeredis.FakeAsyncRedis(decode_responses=True)
        keys = Keys("t:")
        store = RedisStateStore(
            redis,
            PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate),
            clock,
            keys,
        )
        repo = PrismaSubscriptionRepo(lambda: db.litellm_agenteksubscription)
        states = StateService(store, clock, GatewayConfig())
        proxy = SimpleNamespace(db=db)
        models = PrismaModelStore(lambda: db.litellm_proxymodeltable, lambda: proxy)
        login, usage = ScriptedLogin(), ScriptedUsage()
        await _add_model_to_db(
            Deployment(
                model_name="m1",
                litellm_params=LiteLLM_Params(model="chatgpt/m1"),
                model_info=ModelInfo(id="template:m1", mode="responses"),
            ),
            UserAPIKeyAuth(user_id="console"),
            proxy,  # type: ignore[arg-type]
        )
        admin = SubscriptionAdmin(
            AdminDeps(
                clock=clock,
                repo=repo,
                unit=PrismaUnit(lambda: db),  # type: ignore[arg-type]
                runtime=RecordingRuntime(),
                directory=PrismaCredentialDirectory(
                    lambda: db.litellm_credentialstable
                ),
                credentials=PrismaCredentialStore(lambda: db.litellm_credentialstable),
                store=store,
                toggle=SubscriptionToggle(store, repo, states),
                states=states,
                coordinator=TokenCoordinator(redis, keys),
                copies=CopySync(models, repo),
                settings=PrismaProviderSettingsRepo(lambda: db.litellm_config),
                usage_providers={PROVIDER: usage},
                logins={PROVIDER: login},
                on_changed=lambda: None,
                slots=InMemorySlotStore(clock),
                stats=EmptyStats(),
            )
        )

        login.polls = [auth_of("one")]
        created = await admin.login_poll(
            "op", PROVIDER, "d", "u", NewSubscriptionTarget("fresh")
        )
        sub_id = created.subscription.id  # type: ignore[union-attr]
        login.polls = [auth_of("two")]
        await admin.login_poll("op", PROVIDER, "d", "u", ReauthorizeTarget(sub_id))
        await admin.set_enabled("op", sub_id, False)
        await admin.update_settings("op", sub_id, {"priority": 9})
        refreshed = await admin.refresh_limits("op", sub_id)
        listed = await admin.overview()
        assert [m.model_id for m in await models.list_copies()] == [f"sub:{sub_id}:m1"]

        await admin.remove("op", sub_id)

        credential = await db.litellm_credentialstable.find_many()
        stored = json.dumps([row.credential_values for row in credential])
        assert (
            refreshed.refreshed,
            [(v.name, v.priority, v.enabled) for v in listed[1]],
            await db.litellm_agenteksubscription.count(),
            await db.litellm_agenteksubscriptionstate.count(),
            await models.list_copies(),
            credential,
            "access-secret" in stored,
        ) == (True, [("fresh", 9, False)], 0, 0, (), [], False)
        assert await db.litellm_agentekaudit.count() == 6
        actions = [
            row.action
            for row in await db.litellm_agentekaudit.find_many(
                order={"created_at": "asc"}
            )
        ]
        assert actions == [
            "subscription.created",
            "subscription.reauthorized",
            "subscription.enabled",
            "subscription.settings",
            "subscription.limits_refreshed",
            "subscription.removed",
        ]


async def test_a_credential_is_paused_only_when_every_deployment_using_it_is_blocked() -> (
    None
):
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.management_endpoints.model_management_endpoints import (
        _add_model_to_db,
    )
    from litellm.types.router import Deployment, LiteLLM_Params, ModelInfo

    async with live_db() as db:
        proxy = SimpleNamespace(db=db)
        store = PrismaModelStore(lambda: db.litellm_proxymodeltable, lambda: proxy)
        for model_id, credential, blocked in (
            ("d1", "off", True),
            ("d2", "off", True),
            ("d3", "half", True),
            ("d4", "half", False),
            ("sub:x:m", "copy-only", True),
            ("template:m", "tpl-only", True),
        ):
            await _add_model_to_db(
                Deployment(
                    model_name="m",
                    litellm_params=LiteLLM_Params(
                        model="chatgpt/m", litellm_credential_name=credential
                    ),
                    model_info=ModelInfo(id=model_id),
                ),
                UserAPIKeyAuth(user_id="console"),
                proxy,  # type: ignore[arg-type]
            )
            await db.litellm_proxymodeltable.update(
                where={"model_id": model_id}, data={"blocked": blocked}
            )

        assert await store.fully_blocked_credentials() == frozenset({"off"})


class BrokenAudit:
    async def record(self, entry: object) -> None:
        raise RuntimeError("audit down")


def admin_with_broken_audit(db, store, repo, states, redis, keys, clock):  # type: ignore[no-untyped-def]
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def unit():  # type: ignore[no-untyped-def]
        async with db.tx() as tx:
            yield Writes(
                PrismaSubscriptionWriter(lambda: tx.litellm_agenteksubscription),
                PrismaCredentialDirectory(lambda: tx.litellm_credentialstable),
                PrismaProviderSettingsRepo(lambda: tx.litellm_config),
                BrokenAudit(),
            )

    return SubscriptionAdmin(
        AdminDeps(
            clock=clock,
            repo=repo,
            unit=unit,
            directory=PrismaCredentialDirectory(lambda: db.litellm_credentialstable),
            runtime=RecordingRuntime(),
            credentials=PrismaCredentialStore(lambda: db.litellm_credentialstable),
            store=store,
            toggle=SubscriptionToggle(store, repo, states),
            states=states,
            coordinator=TokenCoordinator(redis, keys),
            copies=CopySync(
                PrismaModelStore(
                    lambda: db.litellm_proxymodeltable, lambda: SimpleNamespace(db=db)
                ),
                repo,
            ),
            settings=PrismaProviderSettingsRepo(lambda: db.litellm_config),
            usage_providers={},
            logins={PROVIDER: ScriptedLogin()},
            on_changed=lambda: None,
            slots=InMemorySlotStore(clock),
            stats=EmptyStats(),
        )
    )


async def test_an_action_whose_audit_entry_cannot_be_written_leaves_the_database_untouched() -> (
    None
):
    async with live_db() as db:
        clock, redis = FakeClock(), fakeredis.FakeAsyncRedis(decode_responses=True)
        keys = Keys("t:")
        store = RedisStateStore(
            redis,
            PrismaStateDb(lambda: db.litellm_agenteksubscriptionstate),
            clock,
            keys,
        )
        repo = PrismaSubscriptionRepo(lambda: db.litellm_agenteksubscription)
        states = StateService(store, clock, GatewayConfig())
        directory = PrismaCredentialDirectory(lambda: db.litellm_credentialstable)
        writer = PrismaSubscriptionWriter(lambda: db.litellm_agenteksubscription)
        await directory.create_credential("c", PROVIDER, auth_of("1"))
        created = await writer.create_subscription(NewSubscription(PROVIDER, "n", "c"))
        admin = admin_with_broken_audit(db, store, repo, states, redis, keys, clock)

        with pytest.raises(RuntimeError):
            await admin.remove("op", created.id)  # type: ignore[union-attr]
        with pytest.raises(RuntimeError):
            await admin.update_settings("op", created.id, {"priority": 1})  # type: ignore[union-attr]
        with pytest.raises(RuntimeError):
            await admin.set_provider_concurrency("op", PROVIDER, 4)

        row = await db.litellm_agenteksubscription.find_unique(
            where={"id": created.id}  # type: ignore[union-attr]
        )
        assert (
            await db.litellm_credentialstable.count(),
            row.priority,  # type: ignore[union-attr]
            await db.litellm_config.count(),
        ) == (1, 50, 0)
