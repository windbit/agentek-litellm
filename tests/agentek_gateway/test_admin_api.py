import json
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, Request

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth

from agentek_gateway.api import build_api_router
from agentek_gateway.subscriptions.admin import AdminSlot, SubscriptionAdmin
from agentek_gateway.subscriptions.model import Limits, Window
from agentek_gateway.subscriptions.events import LimitExhausted, LimitWindow
from agentek_gateway.subscriptions.providers.chatgpt_login import ProviderLoginError

from agentek_gateway.subscriptions.unit import Writes, fixed_unit

from .admin_stack import OPERATOR, AdminStack, admin_stack
from .catalog_stack import auth_of, template_row
from .conftest import make_subscription

ADMIN = {"authorization": "admin"}
MEMBER = {"authorization": "member"}
LOGIN = {"provider": "chatgpt", "device_auth_id": "device-1", "user_code": "ABCD-1234"}


def api_client(
    stack: AdminStack | None, admin: SubscriptionAdmin | None = None
) -> httpx.AsyncClient:
    app = FastAPI()
    slot = AdminSlot(admin or (stack.admin if stack else None))
    app.include_router(build_api_router(slot))

    async def fake_auth(request: Request) -> UserAPIKeyAuth:
        role = request.headers.get("authorization", "member")
        return UserAPIKeyAuth(
            user_id=OPERATOR,
            user_role=(
                LitellmUserRoles.PROXY_ADMIN
                if role == "admin"
                else LitellmUserRoles.INTERNAL_USER
            ),
        )

    app.dependency_overrides[user_api_key_auth] = fake_auth
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    )


def seeded(*names: str) -> AdminStack:
    stack = admin_stack([make_subscription(name) for name in names])
    for name in names:
        stack.base.tokens.put(f"cred-{name}", auth_of(name))
    stack.base.models.add_template(template_row("m1"))
    return stack


def secrets_of(*suffixes: str) -> list[str]:
    return [
        f"{kind}-secret-{suffix}"
        for suffix in suffixes
        for kind in ("access", "refresh", "id")
    ]


def action_names(stack: AdminStack) -> list[str]:
    return [entry.action for entry in stack.base.audit.entries]


ROUTES = [
    ("GET", "/agentek/subscriptions", None),
    ("GET", "/agentek/subscriptions/a", None),
    ("PATCH", "/agentek/subscriptions/a", {"priority": 1}),
    ("DELETE", "/agentek/subscriptions/a", None),
    ("PUT", "/agentek/subscriptions/a/enabled", {"enabled": False}),
    ("POST", "/agentek/subscriptions/a/refresh-limits", None),
    ("PUT", "/agentek/subscriptions/providers/chatgpt", {"concurrency_limit": 2}),
    ("POST", "/agentek/subscriptions/login/start", {"provider": "chatgpt"}),
    ("POST", "/agentek/subscriptions/login/poll", {**LOGIN, "name": "x"}),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_a_caller_without_the_admin_role_gets_403_and_changes_nothing(
    method: str, path: str, body: object
) -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        response = await client.request(method, path, json=body, headers=MEMBER)

    assert (response.status_code, "cred-a" in response.text) == (403, False)
    assert (
        stack.base.audit.entries,
        len(await stack.base.repo.list_subscriptions()),
    ) == (
        [],
        1,
    )


async def test_the_api_answers_503_until_the_pool_is_ready() -> None:
    async with api_client(None) as client:
        response = await client.get("/agentek/subscriptions", headers=ADMIN)

    assert response.status_code == 503


async def test_the_list_shows_state_settings_account_and_limits_without_any_token() -> (
    None
):
    stack = seeded("a", "b")
    await stack.base.states.apply(
        stack.base.repo._subscriptions["a"],  # type: ignore[attr-defined]
        LimitExhausted(LimitWindow.WEEKLY, 4_000_000_500.0),
    )
    async with api_client(stack) as client:
        response = await client.get("/agentek/subscriptions", headers=ADMIN)

    body = response.json()
    by_name = {item["name"]: item for item in body["subscriptions"]}
    assert response.status_code == 200
    assert (
        by_name["name-a"]["state"]["state"],
        by_name["name-a"]["state"]["until"] is not None,
        by_name["name-b"]["state"]["state"],
        by_name["name-b"]["priority"],
        by_name["name-b"]["enabled"],
        body["providers"],
    ) == (
        "RATE_LIMITED",
        True,
        "ACTIVE",
        50,
        True,
        [
            {
                "provider": "chatgpt",
                "concurrency_limit": None,
                "subscriptions": 2,
                "working": 1,
            }
        ],
    )
    assert not any(secret in response.text for secret in secrets_of("a", "b"))


async def test_the_account_email_and_plan_come_from_the_id_token() -> None:
    from .test_chatgpt_login import AUTH_CLAIM, jwt

    stack = seeded("a")
    id_token = jwt(
        {"email": "owner@example.test", AUTH_CLAIM: {"chatgpt_plan_type": "pro"}}
    )
    stack.base.tokens.put("cred-a", replace(auth_of("a"), id_token=id_token))
    async with api_client(stack) as client:
        item = (await client.get("/agentek/subscriptions/a", headers=ADMIN)).json()

    assert (item["email"], item["plan"]) == ("owner@example.test", "pro")
    assert id_token not in json.dumps(item)


async def test_an_unknown_subscription_is_404() -> None:
    async with api_client(seeded("a")) as client:
        response = await client.get("/agentek/subscriptions/nope", headers=ADMIN)

    assert response.status_code == 404


async def test_switching_off_disables_and_switching_on_goes_through_a_probe() -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        off = await client.put(
            "/agentek/subscriptions/a/enabled", json={"enabled": False}, headers=ADMIN
        )
        await client.put(
            "/agentek/subscriptions/a/enabled", json={"enabled": False}, headers=ADMIN
        )
        on = await client.put(
            "/agentek/subscriptions/a/enabled", json={"enabled": True}, headers=ADMIN
        )

    assert (off.json()["state"]["state"], off.json()["enabled"]) == ("DISABLED", False)
    assert (on.json()["state"]["state"], on.json()["enabled"]) == ("HALF_OPEN", True)
    assert action_names(stack) == ["subscription.enabled", "subscription.enabled"]
    first = stack.base.audit.entries[0]
    assert (first.actor, first.before, first.after, first.subscription_name) == (
        OPERATOR,
        {"enabled": True},
        {"enabled": False},
        "name-a",
    )


async def test_priority_and_concurrency_are_changed_and_cleared_with_before_and_after() -> (
    None
):
    stack = seeded("a")
    async with api_client(stack) as client:
        changed = await client.patch(
            "/agentek/subscriptions/a",
            json={"priority": 10, "max_concurrency": 3},
            headers=ADMIN,
        )
        cleared = await client.patch(
            "/agentek/subscriptions/a", json={"max_concurrency": None}, headers=ADMIN
        )

    assert (
        (changed.json()["priority"], changed.json()["concurrency_limit"]),
        (cleared.json()["priority"], cleared.json()["concurrency_limit"]),
        [(e.before, e.after) for e in stack.base.audit.entries],
        len(stack.changes),
    ) == (
        (10, 3),
        (10, None),
        [
            (
                {"priority": 50, "max_concurrency": None},
                {"priority": 10, "max_concurrency": 3},
            ),
            ({"max_concurrency": 3}, {"max_concurrency": None}),
        ],
        2,
    )


@pytest.mark.parametrize(
    "body", [{}, {"max_concurrency": 0}, {"priority": None}, {"unknown": 1}]
)
async def test_invalid_settings_are_rejected_and_not_audited(body: object) -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        response = await client.patch(
            "/agentek/subscriptions/a", json=body, headers=ADMIN
        )

    assert (response.status_code, stack.base.audit.entries) == (422, [])


async def test_provider_concurrency_is_stored_audited_and_listed() -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        put = await client.put(
            "/agentek/subscriptions/providers/chatgpt",
            json={"concurrency_limit": 4},
            headers=ADMIN,
        )
        missing = await client.put(
            "/agentek/subscriptions/providers/nope",
            json={"concurrency_limit": 4},
            headers=ADMIN,
        )
        invalid = await client.put(
            "/agentek/subscriptions/providers/chatgpt",
            json={"concurrency_limit": 0},
            headers=ADMIN,
        )

    entry = stack.base.audit.entries[0]
    assert (
        put.json()["concurrency_limit"],
        missing.status_code,
        invalid.status_code,
        (entry.action, entry.subject, entry.before, entry.after),
        (await stack.base.settings.load())["chatgpt"].concurrency_limit,
    ) == (
        4,
        404,
        422,
        (
            "subscription.provider_concurrency",
            "chatgpt",
            {"concurrency_limit": None},
            {"concurrency_limit": 4},
        ),
        4,
    )


async def test_removing_a_subscription_removes_credential_copies_and_state_for_good() -> (
    None
):
    stack = seeded("a", "b")
    await stack.base.copies.run_once()
    await stack.base.toggle.set_enabled(stack.base.repo._subscriptions["a"], False)  # type: ignore[attr-defined]
    await stack.coordinator.save_latest("cred-a", _latest())
    async with api_client(stack) as client:
        gone = await client.delete("/agentek/subscriptions/a", headers=ADMIN)
        again = await client.delete("/agentek/subscriptions/a", headers=ADMIN)
    await stack.base.importer.run("chatgpt")

    assert (gone.status_code, again.status_code) == (204, 404)
    assert [sub.name for sub in await stack.base.repo.list_subscriptions()] == [
        "name-b"
    ]
    assert sorted(stack.base.tokens.values) == ["cred-b"]
    assert sorted(i for i in stack.base.models.rows if i.startswith("sub:")) == [
        "sub:b:m1"
    ]
    assert await stack.base.store.read_state("a") is None
    assert await stack.coordinator.read_latest("cred-a") is None
    assert action_names(stack) == ["subscription.removed"]


def _latest():  # type: ignore[no-untyped-def]
    from agentek_gateway.subscriptions.token_coordination import LatestAuth

    return LatestAuth(auth_of("old"))


async def test_refreshing_limits_asks_the_provider_once_per_thirty_seconds() -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        first = await client.post(
            "/agentek/subscriptions/a/refresh-limits", headers=ADMIN
        )
        stack.base.clock.advance(10)
        second = await client.post(
            "/agentek/subscriptions/a/refresh-limits", headers=ADMIN
        )
        ttl = await stack.base.redis.ttl(stack.base.keys.limits_refresh("a"))
        await stack.base.redis.delete(stack.base.keys.limits_refresh("a"))
        third = await client.post(
            "/agentek/subscriptions/a/refresh-limits", headers=ADMIN
        )

    def stamped(response: httpx.Response) -> object:
        return response.json()["subscription"]["limits"]["observed_at"]

    assert (
        first.json()["refreshed"],
        second.json()["refreshed"],
        third.json()["refreshed"],
        stamped(first) == stamped(second),
        stamped(second) != stamped(third),
        0 < ttl <= 30,
        stack.usage.calls,
        first.json()["subscription"]["limits"]["source"],
    ) == (True, False, True, True, True, True, 2, "usage_check")


async def test_refreshed_limits_at_one_hundred_percent_block_the_subscription() -> None:
    from agentek_gateway.subscriptions.model import Limits, Window

    stack = seeded("a")
    stack.usage.limits = Limits(weekly=Window(100.0, 1_000_000.0 + 3600))
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/a/refresh-limits", headers=ADMIN
        )

    assert response.json()["subscription"]["state"]["state"] == "RATE_LIMITED"


async def test_a_provider_that_gives_no_limits_leaves_the_stored_ones_alone() -> None:
    stack = seeded("a")
    stack.usage.limits = None
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/a/refresh-limits", headers=ADMIN
        )

    assert (
        response.json()["refreshed"],
        response.json()["subscription"]["limits"],
    ) == (
        False,
        None,
    )
    assert stack.base.audit.entries == []


async def test_signing_in_creates_a_subscription_with_its_deployments_and_no_tokens_in_the_answer() -> (
    None
):
    stack = seeded()
    stack.login.polls = [None, auth_of("new")]
    async with api_client(stack) as client:
        started = await client.post(
            "/agentek/subscriptions/login/start",
            json={"provider": "chatgpt"},
            headers=ADMIN,
        )
        pending = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "name": "fresh"},
            headers=ADMIN,
        )
        done = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "name": "fresh"},
            headers=ADMIN,
        )

    assert started.json() == {
        "device_auth_id": "device-1",
        "user_code": "ABCD-1234",
        "verify_url": "https://login.example/device",
    }
    assert (
        pending.json(),
        done.json()["status"],
        done.json()["subscription"]["name"],
    ) == (
        {"status": "pending"},
        "done",
        "fresh",
    )
    assert "fresh" in stack.base.tokens.values
    assert [i for i in stack.base.models.rows if i.startswith("sub:")] != []
    assert action_names(stack) == [
        "subscription.login_started",
        "subscription.created",
    ]
    assert not any(secret in done.text for secret in secrets_of("new"))
    assert stack.changes


@pytest.mark.parametrize(
    "name,expected", [("Bad Name", 422), ("", 422), ("-x", 422), ("taken", 409)]
)
async def test_a_bad_or_taken_name_is_refused_before_any_polling(
    name: str, expected: int
) -> None:
    stack = admin_stack([make_subscription("t", name="taken", credential_name="taken")])
    stack.login.polls = [auth_of("never")]
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "name": name},
            headers=ADMIN,
        )

    assert (response.status_code, len(stack.login.polls)) == (expected, 1)


async def test_a_login_for_an_unknown_provider_is_404() -> None:
    async with api_client(seeded()) as client:
        response = await client.post(
            "/agentek/subscriptions/login/start",
            json={"provider": "nope"},
            headers=ADMIN,
        )

    assert response.status_code == 404


async def test_a_provider_failure_during_login_is_a_502_without_details() -> None:
    stack = seeded()

    async def failing(*_: object) -> None:
        raise ProviderLoginError("device login poll failed, HTTP 500")

    stack.login.poll = failing  # type: ignore[method-assign]
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "name": "x"},
            headers=ADMIN,
        )

    assert response.status_code == 502


async def test_reauthorizing_replaces_tokens_drops_the_shared_pair_and_returns_through_a_probe() -> (
    None
):
    stack = seeded("a")
    subscription = stack.base.repo._subscriptions["a"]  # type: ignore[attr-defined]
    await stack.base.writer.update_subscription("a", {"priority": 7})
    from agentek_gateway.subscriptions.events import TokenRevoked

    await stack.base.states.apply(subscription, TokenRevoked())
    await stack.coordinator.save_latest("cred-a", _latest())
    stack.login.polls = [auth_of("renewed")]
    async with api_client(stack) as client:
        done = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "a"},
            headers=ADMIN,
        )

    stored = await stack.base.tokens.read_auth("cred-a")
    assert (
        done.json()["subscription"]["state"]["state"],
        done.json()["subscription"]["priority"],
        stored.auth.access_token,  # type: ignore[union-attr]
        await stack.coordinator.read_latest("cred-a"),
        action_names(stack),
    ) == ("HALF_OPEN", 7, "access-secret-renewed", None, ["subscription.reauthorized"])
    assert not any(secret in done.text for secret in secrets_of("renewed", "old"))


async def test_a_failed_audit_write_is_not_swallowed() -> None:
    stack = seeded("a")

    class Broken:
        async def record(self, entry: object) -> None:
            raise RuntimeError("audit store down")

    unit = fixed_unit(
        Writes(stack.base.writer, stack.base.directory, stack.base.settings, Broken())
    )
    broken = SubscriptionAdmin(replace(stack.admin._deps, unit=unit))  # type: ignore[attr-defined]
    async with api_client(stack, broken) as client:
        with pytest.raises(RuntimeError):
            await client.put(
                "/agentek/subscriptions/a/enabled",
                json={"enabled": False},
                headers=ADMIN,
            )


async def test_a_pair_a_refresh_leaves_while_tokens_are_replaced_does_not_survive_the_reauthorization() -> (
    None
):
    stack = seeded("a")
    original = stack.base.directory.replace_auth

    async def refresh_lands_during_the_write(name: str, auth: object) -> bool:
        found = await original(name, auth)  # type: ignore[arg-type]
        await stack.coordinator.save_latest(name, _latest())
        return found

    stack.base.directory.replace_auth = refresh_lands_during_the_write  # type: ignore[method-assign]
    stack.login.polls = [auth_of("renewed")]
    async with api_client(stack) as client:
        await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "a"},
            headers=ADMIN,
        )

    assert await stack.coordinator.read_latest("cred-a") is None


async def test_reauthorizing_forgets_models_the_old_account_could_not_serve() -> None:
    stack = seeded("a")
    await stack.base.store.mark_model_unsupported("a", "m1", 3600.0)
    stack.login.polls = [auth_of("renewed")]
    async with api_client(stack) as client:
        await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "a"},
            headers=ADMIN,
        )

    assert await stack.base.store.unsupported_pairs() == frozenset()


async def test_signing_in_again_through_another_provider_is_refused_before_polling() -> (
    None
):
    stack = admin_stack([make_subscription("x", provider="other")])
    stack.login.polls = [auth_of("never")]
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "x"},
            headers=ADMIN,
        )

    assert (response.status_code, len(stack.login.polls)) == (422, 1)


async def test_the_actor_is_the_header_the_console_sends_else_the_key_user() -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        await client.put(
            "/agentek/subscriptions/a/enabled",
            json={"enabled": False},
            headers={**ADMIN, "x-agentek-actor": "console:roman"},
        )
        await client.put(
            "/agentek/subscriptions/a/enabled", json={"enabled": True}, headers=ADMIN
        )
        await client.patch(
            "/agentek/subscriptions/a",
            json={"priority": 1},
            headers={**ADMIN, "x-agentek-actor": "  "},
        )

    assert [entry.actor for entry in stack.base.audit.entries] == [
        "console:roman",
        OPERATOR,
        OPERATOR,
    ]


async def test_the_actor_header_of_a_caller_without_the_admin_role_changes_nothing() -> (
    None
):
    stack = seeded("a")
    async with api_client(stack) as client:
        response = await client.put(
            "/agentek/subscriptions/a/enabled",
            json={"enabled": False},
            headers={**MEMBER, "x-agentek-actor": "console:roman"},
        )

    assert (response.status_code, stack.base.audit.entries) == (403, [])


async def test_switching_on_a_subscription_already_on_repairs_a_state_still_saying_disabled() -> (
    None
):
    stack = seeded("a")
    subscription = stack.base.repo._subscriptions["a"]  # type: ignore[attr-defined]
    await stack.base.toggle.set_enabled(subscription, False)
    await stack.base.writer.update_subscription("a", {"enabled": True})
    async with api_client(stack) as client:
        response = await client.put(
            "/agentek/subscriptions/a/enabled", json={"enabled": True}, headers=ADMIN
        )

    assert (response.json()["state"]["state"], stack.base.audit.entries) == (
        "HALF_OPEN",
        [],
    )


async def test_reauthorizing_a_subscription_whose_credential_is_gone_fails_instead_of_succeeding() -> (
    None
):
    stack = seeded("a")
    stack.base.tokens.values.pop("cred-a")
    stack.login.polls = [auth_of("renewed")]
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "a"},
            headers=ADMIN,
        )

    assert (response.status_code, stack.base.audit.entries) == (404, [])


@pytest.mark.parametrize(
    "target",
    [{}, {"name": "x", "subscription_id": "a"}],
)
async def test_a_sign_in_needs_exactly_one_target(target: dict[str, str]) -> None:
    stack = seeded("a")
    stack.login.polls = [auth_of("never")]
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/login/poll", json={**LOGIN, **target}, headers=ADMIN
        )

    assert (response.status_code, len(stack.login.polls)) == (422, 1)


@pytest.mark.parametrize(
    "body",
    [
        {"priority": 2**31},
        {"priority": True},
        {"max_concurrency": 2**31},
        {"max_concurrency": 1.5},
        {"max_concurrency": True},
    ],
)
async def test_numbers_outside_the_database_range_or_not_numbers_are_refused(
    body: dict[str, object],
) -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        response = await client.patch(
            "/agentek/subscriptions/a", json=body, headers=ADMIN
        )

    assert (response.status_code, stack.base.audit.entries) == (422, [])


async def test_a_provider_concurrency_change_refreshes_the_snapshot() -> None:
    stack = seeded("a")
    async with api_client(stack) as client:
        await client.put(
            "/agentek/subscriptions/providers/chatgpt",
            json={"concurrency_limit": 3},
            headers=ADMIN,
        )

    assert stack.changes


async def test_signing_in_under_the_name_of_an_unimported_credential_is_refused_before_polling() -> (
    None
):
    stack = seeded()
    stack.base.directory.add_empty("orphan")
    stack.login.polls = [auth_of("never")]
    async with api_client(stack) as client:
        response = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "name": "orphan"},
            headers=ADMIN,
        )

    assert (response.status_code, len(stack.login.polls)) == (409, 1)


async def test_the_limits_answer_tells_a_cached_reply_from_a_failed_check() -> None:
    stack = seeded("a", "b")
    stack.usage.limits = None
    async with api_client(stack) as client:
        failed = await client.post(
            "/agentek/subscriptions/a/refresh-limits", headers=ADMIN
        )
        cached = await client.post(
            "/agentek/subscriptions/a/refresh-limits", headers=ADMIN
        )
        stack.usage.limits = Limits(weekly=Window(5.0, 4_000_000_000.0))
        done = await client.post(
            "/agentek/subscriptions/b/refresh-limits", headers=ADMIN
        )

    assert [r.json()["status"] for r in (failed, cached, done)] == [
        "unavailable",
        "cached",
        "refreshed",
    ]


async def test_the_list_reads_the_usage_of_all_subscriptions_once() -> None:
    stack = seeded("a", "b", "c")
    reads = []
    original = stack.base.store.read_all_usage

    async def counting():  # type: ignore[no-untyped-def]
        reads.append(1)
        return await original()

    stack.base.store.read_all_usage = counting  # type: ignore[method-assign]
    async with api_client(stack) as client:
        await client.get("/agentek/subscriptions", headers=ADMIN)

    assert len(reads) == 1


async def test_traffic_is_stopped_before_the_credential_is_deleted() -> None:
    stack = seeded("a")
    seen = []
    original = stack.base.directory.delete_credential

    async def watching(name: str) -> None:
        seen.append((await stack.base.repo.list_subscriptions())[0].enabled)
        await original(name)

    stack.base.directory.delete_credential = watching  # type: ignore[method-assign]
    async with api_client(stack) as client:
        await client.delete("/agentek/subscriptions/a", headers=ADMIN)

    assert seen == [False]


async def test_the_new_tokens_reach_the_runtime_before_the_state_leaves_auth_failed() -> (
    None
):
    stack = seeded("a")
    subscription = stack.base.repo._subscriptions["a"]  # type: ignore[attr-defined]
    from agentek_gateway.subscriptions.events import TokenRevoked

    await stack.base.states.apply(subscription, TokenRevoked())
    order = []
    original = stack.base.states.apply

    async def watching(sub, event):  # type: ignore[no-untyped-def]
        order.append((type(event).__name__, len(stack.runtime.applied)))
        return await original(sub, event)

    stack.admin._deps.states.apply = watching  # type: ignore[method-assign,attr-defined]
    stack.login.polls = [auth_of("renewed")]
    async with api_client(stack) as client:
        await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "a"},
            headers=ADMIN,
        )

    assert order == [("Reauthorized", 1)]
    assert stack.runtime.applied[0][0] == "cred-a"


async def test_a_reauthorization_waits_for_a_refresh_that_holds_the_credential_lock() -> (
    None
):
    stack = seeded("a")
    held = await stack.coordinator.acquire("cred-a")
    short = SubscriptionAdmin(replace(stack.admin._deps, lock_wait_s=0.3))  # type: ignore[attr-defined]
    stack.login.polls = [auth_of("renewed")]
    async with api_client(stack, short) as client:
        refused = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "a"},
            headers=ADMIN,
        )
        await stack.coordinator.release("cred-a", held)  # type: ignore[arg-type]
        stack.login.polls = [auth_of("renewed")]
        done = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "subscription_id": "a"},
            headers=ADMIN,
        )

    assert (refused.status_code, done.status_code) == (409, 200)
    assert await stack.coordinator.acquire("cred-a") is not None


async def test_a_network_failure_while_signing_in_is_a_502() -> None:
    import httpx

    from agentek_gateway.subscriptions.providers.chatgpt_login import ChatgptLogin

    class Unreachable:
        async def post_json(self, *args: object) -> None:
            raise httpx.ConnectError("no route")

        post_form = post_json

    stack = seeded()
    stack.admin._deps.logins["chatgpt"] = ChatgptLogin(Unreachable())  # type: ignore[arg-type,index]
    async with api_client(stack) as client:
        started = await client.post(
            "/agentek/subscriptions/login/start",
            json={"provider": "chatgpt"},
            headers=ADMIN,
        )
        polled = await client.post(
            "/agentek/subscriptions/login/poll",
            json={**LOGIN, "name": "x"},
            headers=ADMIN,
        )

    assert (started.status_code, polled.status_code) == (502, 502)
