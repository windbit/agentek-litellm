"""Cutover: the plugin takes over the deployments an earlier writer made for a subscription credential."""

from types import SimpleNamespace

from agentek_gateway.subscriptions.litellm_deployments import PrismaModelStore
from agentek_gateway.subscriptions.model_copies import (
    LEGACY_GRACE_S,
    CopySync,
    ModelRow,
)

from .catalog_stack import CatalogStack, catalog_stack, template_row
from .conftest import make_subscription
from .live import live_db, needs_postgres


def legacy_row(model_id: str, model: str, credential: str) -> ModelRow:
    return ModelRow(
        model_id=model_id,
        model_name=model,
        litellm_params={
            "model": f"chatgpt/{model}",
            "litellm_credential_name": credential,
        },
        model_info={"id": model_id},
    )


def stack_with_legacy() -> CatalogStack:
    stack = catalog_stack([make_subscription("a"), make_subscription("b")])
    stack.models.add_template(template_row("m1"))
    for sub in ("a", "b"):
        stack.models.add_template(legacy_row(f"console-{sub}-m1", "m1", f"cred-{sub}"))
    stack.copies = CopySync(stack.models, stack.repo, stack.clock)
    return stack


def ids(stack: CatalogStack) -> list[str]:
    return sorted(stack.models.rows)


async def test_a_deployment_stays_until_its_copy_has_stood_for_the_grace() -> None:
    stack = stack_with_legacy()

    await stack.copies.run_once()
    stack.clock.advance(LEGACY_GRACE_S - 1)
    await stack.copies.run_once()

    assert ids(stack) == [
        "console-a-m1",
        "console-b-m1",
        "sub:a:m1",
        "sub:b:m1",
        "template:m1",
    ]


async def test_a_deployment_is_removed_once_its_copy_has_stood_for_the_grace() -> None:
    stack = stack_with_legacy()

    await stack.copies.run_once()
    stack.clock.advance(LEGACY_GRACE_S)
    await stack.copies.run_once()

    assert ids(stack) == ["sub:a:m1", "sub:b:m1", "template:m1"]


async def test_the_pair_is_never_left_without_a_deployment() -> None:
    stack = stack_with_legacy()
    seen: list[list[str]] = []

    for _ in range(6):
        await stack.copies.run_once()
        seen.append([model_id for model_id in ids(stack) if model_id in ("console-a-m1", "sub:a:m1")])
        stack.clock.advance(LEGACY_GRACE_S / 2)

    assert [] not in seen


async def test_a_fresh_replica_waits_the_grace_again_instead_of_trusting_the_database() -> None:
    stack = stack_with_legacy()
    await stack.copies.run_once()
    stack.clock.advance(LEGACY_GRACE_S * 10)
    restarted = CopySync(stack.models, stack.repo, stack.clock)

    await restarted.run_once()

    assert "console-a-m1" in stack.models.rows


async def test_a_deployment_of_a_credential_without_subscription_is_left_alone() -> None:
    stack = stack_with_legacy()
    stack.models.add_template(legacy_row("console-key-m1", "m1", "openrouter-key"))

    await stack.copies.run_once()
    stack.clock.advance(LEGACY_GRACE_S)
    await stack.copies.run_once()

    assert "console-key-m1" in stack.models.rows


async def test_a_deployment_of_a_model_without_template_is_left_alone() -> None:
    stack = stack_with_legacy()
    stack.models.add_template(legacy_row("console-a-old", "old", "cred-a"))

    await stack.copies.run_once()
    stack.clock.advance(LEGACY_GRACE_S)
    await stack.copies.run_once()

    assert "console-a-old" in stack.models.rows


async def test_the_template_is_never_taken_for_a_legacy_deployment() -> None:
    stack = stack_with_legacy()
    stack.models.rows["template:m1"] = ModelRow(
        "template:m1",
        "m1",
        {"model": "chatgpt/m1", "litellm_credential_name": "cred-a"},
        {"id": "template:m1"},
    )

    await stack.copies.run_once()
    stack.clock.advance(LEGACY_GRACE_S)
    await stack.copies.run_once()

    assert "template:m1" in stack.models.rows


async def test_removing_a_subscription_removes_its_legacy_deployments_too() -> None:
    stack = stack_with_legacy()

    await stack.copies.remove_subscription(make_subscription("a"))

    assert "console-a-m1" not in stack.models.rows
    assert "console-b-m1" in stack.models.rows


@needs_postgres
async def test_the_database_store_lists_and_removes_only_deployments_it_does_not_own() -> None:
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.management_endpoints.model_management_endpoints import (
        _add_model_to_db,
    )
    from litellm.types.router import Deployment, LiteLLM_Params, ModelInfo

    async with live_db() as db:
        proxy = SimpleNamespace(db=db)
        store = PrismaModelStore(lambda: db.litellm_proxymodeltable, lambda: proxy)
        for model_id, credential in (
            ("template:m1", None),
            ("sub:a:m1", "cred-a"),
            ("console-1", "cred-a"),
            ("console-2", None),
        ):
            params = {"model": "chatgpt/m1"}
            if credential:
                params["litellm_credential_name"] = credential
            await _add_model_to_db(
                Deployment(
                    model_name="m1",
                    litellm_params=LiteLLM_Params(**params),
                    model_info=ModelInfo(id=model_id),
                ),
                UserAPIKeyAuth(user_id="console"),
                proxy,  # type: ignore[arg-type]
            )

        listed = [row.model_id for row in await store.list_legacy()]
        await store.delete_legacy(["console-1", "template:m1", "sub:a:m1"])
        left = sorted(record.model_id for record in await db.litellm_proxymodeltable.find_many())

        assert (listed, left) == (["console-1"], ["console-2", "sub:a:m1", "template:m1"])


async def test_requests_keep_succeeding_while_the_router_follows_every_cutover_step() -> None:
    from litellm import Router

    stack = stack_with_legacy()
    router = Router(
        model_list=[_as_mock_deployment(row) for row in stack.models.rows.values()],
        num_retries=0,
    )
    failures = 0

    for _ in range(6):
        await stack.copies.run_once()
        router.set_model_list(
            [
                _as_mock_deployment(row)
                for row in stack.models.rows.values()
                if row.litellm_params.get("litellm_credential_name")
            ]
        )
        for _ in range(5):
            try:
                await router.acompletion(model="m1", messages=[{"role": "user", "content": "x"}])
            except Exception:  # noqa: BLE001
                failures += 1
        stack.clock.advance(LEGACY_GRACE_S / 2)

    assert (failures, ids(stack)) == (0, ["sub:a:m1", "sub:b:m1", "template:m1"])


def _as_mock_deployment(row: ModelRow) -> dict[str, object]:
    return {
        "model_name": row.model_name,
        "litellm_params": {
            "model": "openai/fake",
            "api_key": "k",
            "mock_response": "fine",
        },
        "model_info": {"id": row.model_id},
    }
