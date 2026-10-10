import asyncio
import json
from types import SimpleNamespace

from agentek_gateway.subscriptions.duties import LeaderDuties
from agentek_gateway.subscriptions.litellm_deployments import PrismaModelStore
from agentek_gateway.subscriptions.model_copies import (
    COPY_SYNC_INTERVAL_S,
    CopySync,
    ModelRow,
    plan_copies,
)
from agentek_gateway.subscriptions.memory import InMemorySubscriptionRepo
from agentek_gateway.subscriptions.memory_catalog import InMemoryModelStore

from .catalog_stack import catalog_stack, template_row
from .conftest import make_subscription
from .live import live_db, needs_postgres

ROUTER_RELOAD_S = 30.0
COPIES_BUDGET_S = 60.0


def copy_ids(models: InMemoryModelStore) -> list[str]:
    return sorted(model_id for model_id in models.rows if model_id.startswith("sub:"))


async def test_every_subscription_gets_a_copy_of_every_template_of_its_provider() -> (
    None
):
    stack = catalog_stack([make_subscription("a"), make_subscription("b")])
    stack.models.add_template(template_row("m1"))
    stack.models.add_template(template_row("m2"))

    await stack.copies.run_once()

    assert copy_ids(stack.models) == [
        "sub:a:m1",
        "sub:a:m2",
        "sub:b:m1",
        "sub:b:m2",
    ]


async def test_a_copy_carries_the_template_and_the_credential_of_its_subscription() -> (
    None
):
    stack = catalog_stack([make_subscription("a")])
    stack.models.add_template(template_row("m1", price=2e-06))

    await stack.copies.run_once()

    copy = stack.models.rows["sub:a:m1"]
    assert (copy.model_name, dict(copy.litellm_params), dict(copy.model_info)) == (
        "m1",
        {
            "model": "chatgpt/m1",
            "input_cost_per_token": 2e-06,
            "litellm_credential_name": "cred-a",
        },
        {"id": "sub:a:m1", "mode": "responses"},
    )


async def test_templates_of_another_provider_are_not_copied() -> None:
    stack = catalog_stack([make_subscription("a")])
    other = ModelRow("template:x", "x", {"model": "openai/x"}, {"id": "template:x"})
    stack.models.add_template(other)

    await stack.copies.run_once()

    assert copy_ids(stack.models) == []


async def test_a_second_pass_writes_nothing() -> None:
    stack = catalog_stack([make_subscription("a")])
    stack.models.add_template(template_row("m1"))
    await stack.copies.run_once()
    writes = stack.models.writes

    plan = await stack.copies.run_once()

    assert (stack.models.writes, plan.create, plan.update, plan.delete) == (
        writes,
        (),
        (),
        (),
    )


async def test_two_replicas_and_a_repeated_pass_leave_exactly_one_deployment_per_pair() -> (
    None
):
    stack = catalog_stack([make_subscription("a"), make_subscription("b")])
    stack.models.add_template(template_row("m1"))
    stack.models.add_template(template_row("m2"))
    replica = CopySync(stack.models, stack.repo)

    await asyncio.gather(stack.copies.run_once(), replica.run_once())
    await stack.copies.run_once()
    await replica.run_once()

    assert copy_ids(stack.models) == [
        "sub:a:m1",
        "sub:a:m2",
        "sub:b:m1",
        "sub:b:m2",
    ]


async def test_a_replica_that_lost_the_creation_race_still_applies_its_own_template_view() -> (
    None
):
    stack = catalog_stack([make_subscription("a")])
    stack.models.add_template(template_row("m1", price=1e-06))
    stale = ModelRow(
        "sub:a:m1",
        "m1",
        {
            "model": "chatgpt/m1",
            "input_cost_per_token": 9e-06,
            "litellm_credential_name": "cred-a",
        },
        {"id": "sub:a:m1", "mode": "responses"},
    )

    class ListsBeforeTheOtherReplicaWrote(InMemoryModelStore):
        async def list_copies(self):  # type: ignore[no-untyped-def]
            return ()

    models = ListsBeforeTheOtherReplicaWrote()
    models.rows = {**stack.models.rows, "sub:a:m1": stale}

    await CopySync(models, stack.repo).run_once()

    assert models.rows["sub:a:m1"].litellm_params["input_cost_per_token"] == 1e-06


async def test_a_changed_template_description_reaches_the_copies() -> None:
    stack = catalog_stack([make_subscription("a")])
    stack.models.add_template(template_row("m1"))
    await stack.copies.run_once()
    changed = template_row("m1")
    stack.models.add_template(
        ModelRow(
            changed.model_id,
            changed.model_name,
            changed.litellm_params,
            {**changed.model_info, "max_input_tokens": 272000},
        )
    )

    await stack.copies.run_once()

    assert stack.models.rows["sub:a:m1"].model_info["max_input_tokens"] == 272000


async def test_bookkeeping_fields_of_the_template_are_neither_copied_nor_cause_rewrites() -> (
    None
):
    stack = catalog_stack([make_subscription("a")])
    base = template_row("m1")
    stack.models.add_template(
        ModelRow(
            base.model_id,
            base.model_name,
            base.litellm_params,
            {
                **base.model_info,
                "blocked": True,
                "updated_at": "2026-10-01T00:00:00",
                "updated_by": "console",
            },
        )
    )
    await stack.copies.run_once()
    writes = stack.models.writes

    await stack.copies.run_once()

    assert (
        dict(stack.models.rows["sub:a:m1"].model_info),
        stack.models.writes,
    ) == ({"id": "sub:a:m1", "mode": "responses"}, writes)


async def test_a_new_discount_reaches_every_copy_within_the_budget() -> None:
    stack = catalog_stack([make_subscription("a"), make_subscription("b")])
    stack.models.add_template(template_row("m1", price=1e-06))
    duties = _duties(stack)
    await duties.tick()
    stack.models.add_template(template_row("m1", price=2.5e-06))

    stack.clock.advance(COPY_SYNC_INTERVAL_S)
    await duties.tick()

    prices = {
        model_id: row.litellm_params["input_cost_per_token"]
        for model_id, row in stack.models.rows.items()
        if model_id.startswith("sub:")
    }
    assert prices == {"sub:a:m1": 2.5e-06, "sub:b:m1": 2.5e-06}


def test_the_copy_pass_and_the_router_reload_fit_the_sixty_second_budget() -> None:
    assert COPY_SYNC_INTERVAL_S + ROUTER_RELOAD_S <= COPIES_BUDGET_S


async def test_deleting_a_template_deletes_its_copies_everywhere() -> None:
    stack = catalog_stack([make_subscription("a"), make_subscription("b")])
    stack.models.add_template(template_row("m1"))
    stack.models.add_template(template_row("m2"))
    await stack.copies.run_once()

    del stack.models.rows["template:m1"]
    await stack.copies.run_once()

    assert copy_ids(stack.models) == ["sub:a:m2", "sub:b:m2"]


async def test_deleting_a_subscription_deletes_its_copies_and_keeps_the_others() -> (
    None
):
    stack = catalog_stack([make_subscription("a"), make_subscription("b")])
    stack.models.add_template(template_row("m1"))
    await stack.copies.run_once()

    stack.repo.remove("a")
    await stack.copies.run_once()

    assert copy_ids(stack.models) == ["sub:b:m1"]


async def test_a_subscription_added_while_a_pass_runs_keeps_its_copies() -> None:
    repo = InMemorySubscriptionRepo([make_subscription("a")])

    class AddsSubscriptionBeforeListing(InMemoryModelStore):
        raced = False

        async def list_copies(self):  # type: ignore[no-untyped-def]
            if not self.raced:
                self.raced = True
                repo.put(make_subscription("late"))
                await CopySync(self, repo).run_once()
            return await super().list_copies()

    models = AddsSubscriptionBeforeListing()
    models.add_template(template_row("m1"))

    await CopySync(models, repo).run_once()

    assert copy_ids(models) == ["sub:a:m1", "sub:late:m1"]


async def test_removing_a_subscription_removes_only_its_own_copies() -> None:
    stack = catalog_stack([make_subscription("a"), make_subscription("ab")])
    stack.models.add_template(template_row("m1"))
    await stack.copies.run_once()

    await stack.copies.remove_subscription(make_subscription("a"))

    assert copy_ids(stack.models) == ["sub:ab:m1"]


def _duties(stack):  # type: ignore[no-untyped-def]
    from agentek_gateway.subscriptions.catalog import CatalogUpkeep

    class Lease:
        async def hold(self) -> bool:
            return True

    class Idle:
        async def tick(self) -> None:
            return None

    upkeep = CatalogUpkeep(stack.importer, stack.copies, ["chatgpt"])
    return LeaderDuties(Lease(), Idle(), Idle(), Idle(), stack.clock, upkeep)  # type: ignore[arg-type]


@needs_postgres
async def test_on_a_real_database_copies_are_encrypted_idempotent_and_follow_the_template() -> (
    None
):
    from litellm.types.router import Deployment, LiteLLM_Params, ModelInfo
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.management_endpoints.model_management_endpoints import (
        _add_model_to_db,
    )

    async with live_db() as db:
        proxy = SimpleNamespace(db=db)
        store = PrismaModelStore(lambda: db.litellm_proxymodeltable, lambda: proxy)
        repo = InMemorySubscriptionRepo(
            [make_subscription("a"), make_subscription("b")]
        )
        for name in ("m1", "m2"):
            await _add_model_to_db(
                Deployment(
                    model_name=name,
                    litellm_params=LiteLLM_Params(
                        model=f"chatgpt/{name}", input_cost_per_token=1e-06
                    ),
                    model_info=ModelInfo(id=f"template:{name}", mode="responses"),
                ),
                UserAPIKeyAuth(user_id="console"),
                proxy,  # type: ignore[arg-type]
            )
            await db.litellm_proxymodeltable.update(
                where={"model_id": f"template:{name}"}, data={"blocked": True}
            )
        first, second = CopySync(store, repo), CopySync(store, repo)

        await asyncio.gather(first.run_once(), second.run_once())
        await first.run_once()
        rows = await db.litellm_proxymodeltable.find_many(
            where={"model_id": {"startswith": "sub:"}}, order={"model_id": "asc"}
        )
        assert [row.model_id for row in rows] == [
            "sub:a:m1",
            "sub:a:m2",
            "sub:b:m1",
            "sub:b:m2",
        ]
        assert all(row.blocked is False for row in rows)
        assert all("chatgpt/" not in json.dumps(row.litellm_params) for row in rows)

        templates = {row.model_id: row for row in await store.list_templates()}
        assert templates["template:m1"].litellm_params["model"] == "chatgpt/m1"
        writes_before = [row.updated_at for row in rows]
        await first.run_once()
        again = await db.litellm_proxymodeltable.find_many(
            where={"model_id": {"startswith": "sub:"}}, order={"model_id": "asc"}
        )
        assert [row.updated_at for row in again] == writes_before

        await db.litellm_proxymodeltable.update(
            where={"model_id": "template:m1"},
            data={
                "litellm_params": json.dumps(
                    await _encrypted_params(proxy, "m1", price=7e-06)
                )
            },
        )
        await first.run_once()
        repriced = {row.model_id: row for row in await store.list_copies()}
        assert {
            model_id: row.litellm_params["input_cost_per_token"]
            for model_id, row in repriced.items()
        } == {
            "sub:a:m1": 7e-06,
            "sub:a:m2": 1e-06,
            "sub:b:m1": 7e-06,
            "sub:b:m2": 1e-06,
        }
        assert (
            repriced["sub:a:m1"].litellm_params["litellm_credential_name"] == "cred-a"
        )

        row = (await store.list_copies())[0]
        assert await store.create_copy(row) is False
        await store.delete_copies(["template:m1", "template:m2"])
        assert sorted(item.model_id for item in await store.list_templates()) == [
            "template:m1",
            "template:m2",
        ]

        await db.litellm_proxymodeltable.delete_many(where={"model_id": "template:m2"})
        repo.remove("b")
        await first.run_once()
        assert sorted(row.model_id for row in await store.list_copies()) == ["sub:a:m1"]


async def _encrypted_params(proxy, name: str, price: float):  # type: ignore[no-untyped-def]
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.management_endpoints.model_management_endpoints import (
        _add_model_to_db,
    )
    from litellm.types.router import Deployment, LiteLLM_Params, ModelInfo

    stored = await _add_model_to_db(
        Deployment(
            model_name=name,
            litellm_params=LiteLLM_Params(
                model=f"chatgpt/{name}", input_cost_per_token=price
            ),
            model_info=ModelInfo(id=f"template:{name}", mode="responses"),
        ),
        UserAPIKeyAuth(user_id="console"),
        proxy,
        should_create_model_in_db=False,
    )
    return stored.litellm_params
