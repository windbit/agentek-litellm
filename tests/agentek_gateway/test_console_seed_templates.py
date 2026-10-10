"""Deployments the operator console seeds, copied by the real CopySync onto a real Postgres.

The console POSTs one blocked `template:<model>` per subscription model to /model/new (and, in its rollback mode, one
deployment per credential). The golden file in the console repo holds those exact bodies; without a console checkout
a built-in sample of the same shape stands in, so the copy path is still exercised.
"""

import json
from collections.abc import Mapping
from types import SimpleNamespace

from agentek_gateway.subscriptions.litellm_deployments import PrismaModelStore
from agentek_gateway.subscriptions.memory import InMemorySubscriptionRepo
from agentek_gateway.subscriptions.model_copies import (
    CREDENTIAL_PARAM,
    LEGACY_GRACE_S,
    VOLATILE_INFO_KEYS,
    CopySync,
)

from .conftest import FakeClock, make_subscription
from .console_golden import find_golden
from .live import live_db, needs_postgres

GOLDEN_NAME = "seedDeployments.json"
PRICE_PARAMS = ("input_cost_per_token", "output_cost_per_token")
LEGACY_CREDENTIAL = "chatgpt-legacy-a"
SUBSCRIPTION_IDS = ("a", "b")

SAMPLE_BODY = {
    "model_name": "sample-model",
    "litellm_params": {
        "model": "chatgpt/sample-model",
        "input_cost_per_token": 2e-07,
        "output_cost_per_token": 1e-06,
        "cache_read_input_token_cost": 2e-08,
        "allowed_openai_params": ["reasoning_effort"],
    },
    "model_info": {
        "id": "template:sample-model",
        "mode": "responses",
        "max_input_tokens": 272000,
        "max_output_tokens": 128000,
        "max_tokens": 128000,
        "supports_reasoning": True,
        "supports_function_calling": True,
    },
}
SAMPLE_GOLDEN = {
    "templates": [
        {
            "newModel": SAMPLE_BODY,
            "block": {
                "method": "PATCH",
                "path": "/model/template:sample-model/update",
                "body": {"blocked": True},
            },
        }
    ],
    "legacy": {
        "credentialName": LEGACY_CREDENTIAL,
        "newModels": [
            {
                "model_name": "sample-model",
                "litellm_params": {
                    **SAMPLE_BODY["litellm_params"],  # type: ignore[dict-item]
                    CREDENTIAL_PARAM: LEGACY_CREDENTIAL,
                },
                "model_info": {
                    key: value
                    for key, value in SAMPLE_BODY["model_info"].items()  # type: ignore[attr-defined]
                    if key != "id"
                },
            }
        ],
    },
}


def seed_golden() -> dict:  # type: ignore[type-arg]
    path = find_golden(GOLDEN_NAME)
    return json.loads(path.read_text()) if path else SAMPLE_GOLDEN


async def post_new_model(proxy: SimpleNamespace, body: Mapping[str, object]) -> str:
    """What the gateway does with the console's POST /model/new: parse a Deployment, write it through LiteLLM's writer."""
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.management_endpoints.model_management_endpoints import (
        _add_model_to_db,
    )
    from litellm.types.router import Deployment

    stored = await _add_model_to_db(
        model_params=Deployment(**body),  # type: ignore[arg-type]
        user_api_key_dict=UserAPIKeyAuth(user_id="console"),
        prisma_client=proxy,  # type: ignore[arg-type]
    )
    assert stored is not None
    return stored.model_id


async def patch_model(
    proxy: SimpleNamespace, model_id: str, patch: Mapping[str, object]
) -> None:
    """What the gateway does with the console's PATCH /model/<id>/update, minus authorization."""
    from litellm.proxy.management_endpoints.model_management_endpoints import (
        get_db_model,
        update_db_model,
    )
    from litellm.types.router import updateDeployment

    db_model = await get_db_model(model_id=model_id, prisma_client=proxy)  # type: ignore[arg-type]
    assert db_model is not None
    data = update_db_model(db_model=db_model, updated_patch=updateDeployment(**patch))
    await proxy.db.litellm_proxymodeltable.update(
        where={"model_id": model_id}, data=data
    )


async def seed_templates(proxy: SimpleNamespace, golden: dict) -> None:  # type: ignore[type-arg]
    for template in golden["templates"]:
        model_id = await post_new_model(proxy, template["newModel"])
        assert template["block"]["path"] == f"/model/{model_id}/update"
        await patch_model(proxy, model_id, template["block"]["body"])


def stable(info: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in info.items() if key not in VOLATILE_INFO_KEYS}


# Breaks when: the copy of a template the console seeded loses or changes its model, price or model_info on the way
@needs_postgres
async def test_a_copy_of_a_seeded_template_carries_model_price_and_model_info() -> None:
    golden = seed_golden()
    async with live_db() as db:
        proxy = SimpleNamespace(db=db)
        await seed_templates(proxy, golden)
        store = PrismaModelStore(lambda: db.litellm_proxymodeltable, lambda: proxy)
        repo = InMemorySubscriptionRepo(
            [make_subscription(sub_id) for sub_id in SUBSCRIPTION_IDS]
        )

        await CopySync(store, repo).run_once()

        copies = {row.model_id: row for row in await store.list_copies()}
        templates = {row.model_id: row for row in await store.list_templates()}
        blocked = {
            row.model_id: row.blocked
            for row in await db.litellm_proxymodeltable.find_many()
        }
    expected_ids = {
        f"sub:{sub_id}:{template['newModel']['model_name']}"
        for sub_id in SUBSCRIPTION_IDS
        for template in golden["templates"]
    }
    assert set(copies) == expected_ids
    for template in golden["templates"]:
        body = template["newModel"]
        stored = templates[body["model_info"]["id"]]
        assert all(name in body["litellm_params"] for name in PRICE_PARAMS)
        assert blocked[stored.model_id] is True
        for sub_id in SUBSCRIPTION_IDS:
            copy = copies[f"sub:{sub_id}:{body['model_name']}"]
            credential = {CREDENTIAL_PARAM: f"cred-{sub_id}"}
            assert {
                "name": copy.model_name,
                "params_carry_the_body": {
                    **body["litellm_params"],
                    **credential,
                }.items()
                <= copy.litellm_params.items(),
                "params_equal_the_stored_template": dict(copy.litellm_params)
                == {**stored.litellm_params, **credential},
                "info_carries_the_body": stable(body["model_info"]).items()
                <= stable(copy.model_info).items(),
                "info_carries_the_stored_template": stable(stored.model_info).items()
                <= stable(copy.model_info).items(),
                "info_price_agrees": all(
                    copy.model_info.get(name, body["litellm_params"][name])
                    == body["litellm_params"][name]
                    for name in PRICE_PARAMS
                ),
                "id": copy.model_info["id"],
                "blocked": blocked[copy.model_id],
            } == {
                "name": body["model_name"],
                "params_carry_the_body": True,
                "params_equal_the_stored_template": True,
                "info_carries_the_body": True,
                "info_carries_the_stored_template": True,
                "info_price_agrees": True,
                "id": copy.model_id,
                "blocked": False,
            }


# Breaks when: a rollback-matrix deployment of the console is not retired after the grace, or the pair is ever unserved
@needs_postgres
async def test_a_deployment_the_console_made_per_credential_is_retired_after_the_grace() -> (
    None
):
    golden = seed_golden()
    legacy = golden["legacy"]
    names = sorted(body["model_name"] for body in legacy["newModels"])
    async with live_db() as db:
        proxy = SimpleNamespace(db=db)
        await seed_templates(proxy, golden)
        for body in legacy["newModels"]:
            await post_new_model(proxy, body)
        store = PrismaModelStore(lambda: db.litellm_proxymodeltable, lambda: proxy)
        repo = InMemorySubscriptionRepo(
            [make_subscription("a", credential_name=legacy["credentialName"])]
        )
        clock = FakeClock()
        sync = CopySync(store, repo, clock)

        async def serving() -> dict[str, list[str]]:
            rows = [*await store.list_copies(), *await store.list_legacy()]
            return {
                name: sorted(
                    row.model_id
                    for row in rows
                    if row.model_name == name
                    and row.litellm_params.get(CREDENTIAL_PARAM)
                    == legacy["credentialName"]
                )
                for name in names
            }

        await sync.run_once()
        clock.advance(LEGACY_GRACE_S - 1)
        await sync.run_once()
        inside_grace = await serving()
        clock.advance(1)
        await sync.run_once()
        after_grace = await serving()
        left_over = await store.list_legacy()

    assert all(len(ids) == 2 for ids in inside_grace.values())
    assert after_grace == {name: [f"sub:a:{name}"] for name in names}
    assert left_over == ()
