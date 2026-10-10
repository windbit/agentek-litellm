import logging

import pytest

from agentek_gateway.subscriptions.credential_directory import (
    PrismaCredentialDirectory,
)
from agentek_gateway.subscriptions.importer import IMPORT_ACTION, CredentialImporter
from agentek_gateway.subscriptions.model import SubscriptionState as S
from agentek_gateway.subscriptions.toggle import SubscriptionToggle
from agentek_gateway.subscriptions.prisma_repos import (
    PrismaSubscriptionRepo,
    PrismaSubscriptionWriter,
)

from .catalog_stack import PROVIDER, auth_of, catalog_stack
from .conftest import make_subscription
from .live import live_db, needs_postgres


async def states_of(stack) -> dict[str, S]:  # type: ignore[no-untyped-def]
    subscriptions = await stack.repo.list_subscriptions()
    records = await stack.store.read_states([sub.id for sub in subscriptions])
    return {
        sub.name: records[sub.id].state if sub.id in records else S.ACTIVE
        for sub in subscriptions
    }


async def test_every_credential_with_tokens_becomes_a_subscription_under_its_name() -> (
    None
):
    stack = catalog_stack()
    stack.tokens.put("team-a", auth_of("a"))
    stack.tokens.put("team-b", auth_of("b"))

    report = await stack.importer.run(PROVIDER)

    subscriptions = await stack.repo.list_subscriptions()
    assert (
        sorted((sub.name, sub.credential_name, sub.priority) for sub in subscriptions),
        report.imported,
    ) == (
        [("team-a", "team-a", 50), ("team-b", "team-b", 50)],
        ("team-a", "team-b"),
    )


async def test_running_again_changes_nothing() -> None:
    stack = catalog_stack()
    stack.tokens.put("team-a", auth_of())
    await stack.importer.run(PROVIDER)
    before = await stack.repo.list_subscriptions()

    second = await stack.importer.run(PROVIDER)

    assert (await stack.repo.list_subscriptions(), second.imported) == (before, ())


async def test_a_disabled_credential_becomes_a_disabled_subscription() -> None:
    stack = catalog_stack()
    stack.tokens.put("on", auth_of("1"))
    stack.tokens.put("off", auth_of("2"))
    stack.directory.disabled.add("off")

    await stack.importer.run(PROVIDER)

    subscriptions = {sub.name: sub for sub in await stack.repo.list_subscriptions()}
    assert (
        subscriptions["off"].enabled,
        subscriptions["on"].enabled,
        await states_of(stack),
    ) == (False, True, {"on": S.ACTIVE, "off": S.DISABLED})


async def test_a_credential_without_tokens_is_skipped_and_named_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stack = catalog_stack()
    stack.directory.add_empty("fresh-and-empty")

    with caplog.at_level(logging.WARNING):
        report = await stack.importer.run(PROVIDER)
        await stack.importer.run(PROVIDER)

    skipped = [
        record.getMessage()
        for record in caplog.records
        if "fresh-and-empty" in record.getMessage()
    ]
    assert (
        tuple(await stack.repo.list_subscriptions()),
        report.skipped_without_tokens,
        len(skipped),
    ) == ((), ("fresh-and-empty",), 1)


async def test_a_credential_gets_imported_once_tokens_appear() -> None:
    stack = catalog_stack()
    stack.directory.add_empty("late")
    await stack.importer.run(PROVIDER)
    stack.directory.empty.discard("late")
    stack.tokens.put("late", auth_of())

    report = await stack.importer.run(PROVIDER)

    assert report.imported == ("late",)


async def test_the_import_is_written_to_the_audit_log_as_the_system() -> None:
    stack = catalog_stack()
    stack.tokens.put("team-a", auth_of())

    await stack.importer.run(PROVIDER)

    (entry,) = stack.audit.entries
    assert (entry.actor, entry.action, entry.subscription_name) == (
        "system",
        IMPORT_ACTION,
        "team-a",
    )


async def test_a_subscription_created_by_another_replica_counts_as_existing() -> None:
    stack = catalog_stack()
    stack.tokens.put("team-a", auth_of())

    class Racing:
        async def list_subscriptions(self):  # type: ignore[no-untyped-def]
            return ()

        async def set_enabled(self, *args):  # type: ignore[no-untyped-def]
            raise AssertionError

    racing = CredentialImporter(
        stack.directory, Racing(), stack.writer, stack.toggle, stack.audit  # type: ignore[arg-type]
    )
    await stack.importer.run(PROVIDER)

    report = await racing.run(PROVIDER)

    assert (len(await stack.repo.list_subscriptions()), report.imported) == (1, ())


class RecordingWriter:
    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self.created = []

    async def create_subscription(self, new):  # type: ignore[no-untyped-def]
        self.created.append(new)
        return await self._inner.create_subscription(new)


def importer_with(stack, writer):  # type: ignore[no-untyped-def]
    return CredentialImporter(
        stack.directory, stack.repo, writer, stack.toggle, stack.audit
    )


async def test_a_disabled_credential_is_created_disabled_never_enabled_first() -> None:
    stack = catalog_stack()
    stack.tokens.put("off", auth_of())
    stack.directory.disabled.add("off")
    writer = RecordingWriter(stack.writer)

    await importer_with(stack, writer).run(PROVIDER)

    assert [(new.name, new.enabled) for new in writer.created] == [("off", False)]


async def test_a_credential_that_is_already_a_subscription_is_not_created_again() -> (
    None
):
    stack = catalog_stack()
    stack.tokens.put("team-a", auth_of())
    await stack.importer.run(PROVIDER)
    writer = RecordingWriter(stack.writer)

    await importer_with(stack, writer).run(PROVIDER)

    assert writer.created == []


async def test_a_credential_behind_a_renamed_subscription_is_not_imported_twice() -> (
    None
):
    stack = catalog_stack(
        [make_subscription("s", name="pretty", credential_name="team-a")]
    )
    stack.tokens.put("team-a", auth_of())

    report = await stack.importer.run(PROVIDER)

    assert (
        report.imported,
        [sub.name for sub in await stack.repo.list_subscriptions()],
    ) == (
        (),
        ["pretty"],
    )


@needs_postgres
async def test_on_a_real_database_two_replicas_import_each_credential_once() -> None:
    import asyncio
    import json

    async with live_db() as db:
        stack = catalog_stack()
        for name, extra in (
            ("one", {}),
            ("off", {"disabled": True}),
            ("claude-like", {"custom_llm_provider": "other"}),
        ):
            await db.litellm_credentialstable.create(
                data={
                    "credential_name": name,
                    "credential_values": json.dumps(
                        {"chatgpt_auth": {"access_token": "a", "refresh_token": "r"}}
                    ),
                    "credential_info": json.dumps(
                        {"custom_llm_provider": PROVIDER, **extra}
                    ),
                    "created_by": "t",
                    "updated_by": "t",
                }
            )
        await db.litellm_credentialstable.create(
            data={
                "credential_name": "empty",
                "credential_values": json.dumps({}),
                "credential_info": json.dumps({"custom_llm_provider": PROVIDER}),
                "created_by": "t",
                "updated_by": "t",
            }
        )
        repo = PrismaSubscriptionRepo(lambda: db.litellm_agenteksubscription)

        def replica() -> CredentialImporter:
            return CredentialImporter(
                PrismaCredentialDirectory(lambda: db.litellm_credentialstable),
                repo,
                PrismaSubscriptionWriter(lambda: db.litellm_agenteksubscription),
                SubscriptionToggle(stack.store, repo, stack.states),
                stack.audit,
            )

        first, second = await asyncio.gather(
            replica().run(PROVIDER), replica().run(PROVIDER)
        )
        await replica().run(PROVIDER)

        rows = await db.litellm_agenteksubscription.find_many(order={"name": "asc"})
        assert (
            [(row.name, row.credential_name, row.enabled) for row in rows],
            len(first.imported) + len(second.imported),
        ) == ([("off", "off", False), ("one", "one", True)], 2)
