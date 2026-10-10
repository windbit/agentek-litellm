"""The wire contract of /agentek/subscriptions as the operator console reads and writes it.

The console client maps these bodies field by field, so a renamed or dropped field breaks it without any error here.
test_wire_field_lists pins the field lists literally and always runs.
test_replayed_console_calls replays the console's recorded calls against the real router when the console's golden
file is reachable (see console_golden.py); AGENTEK_UPDATE_WIRE_GOLDEN=1 rewrites the recorded answers from the router.
"""

import json
import os
from dataclasses import replace

import httpx

from agentek_gateway.subscriptions.events import LimitExhausted, LimitWindow
from agentek_gateway.subscriptions.model import Limits, SubscriptionState, Window

from .admin_stack import AdminStack, admin_stack
from .catalog_stack import auth_of, template_row
from .conftest import make_subscription
from .console_golden import golden_file
from .test_admin_api import ADMIN, api_client
from .test_chatgpt_login import AUTH_CLAIM, jwt

GOLDEN_NAME = "gatewaySubscriptionsWire.json"
UPDATE_ENV = "AGENTEK_UPDATE_WIRE_GOLDEN"
ACTOR = "console-admin-7"
ACTOR_HEADER = "x-agentek-actor"

SUBSCRIPTION_FIELDS = {
    "id",
    "provider",
    "name",
    "email",
    "plan",
    "enabled",
    "priority",
    "concurrency_limit",
    "state",
    "limits",
}
STATE_FIELDS = {"state", "reason", "source", "until", "entered_at"}
LIMITS_FIELDS = {"five_hour", "weekly", "observed_at", "source"}
WINDOW_FIELDS = {"used_percent", "reset_at"}
PROVIDER_FIELDS = {"provider", "concurrency_limit", "subscriptions", "working"}
LOGIN_START_FIELDS = {"device_auth_id", "user_code", "verify_url"}
REFRESH_FIELDS = {"refreshed", "status", "subscription"}
LOGIN_POLL = {
    "provider": "chatgpt",
    "device_auth_id": "device-1",
    "user_code": "ABCD-1234",
}


def wire_stack() -> AdminStack:
    stack = admin_stack([make_subscription("a"), make_subscription("b")])
    id_token = jwt(
        {"email": "owner@example.test", AUTH_CLAIM: {"chatgpt_plan_type": "pro"}}
    )
    stack.base.tokens.put("cred-a", replace(auth_of("a"), id_token=id_token))
    stack.base.tokens.put("cred-b", auth_of("b"))
    stack.base.models.add_template(template_row("m1"))
    stack.login.polls = [None, auth_of("fresh"), auth_of("renewed")]
    return stack


async def seeded_wire_stack() -> AdminStack:
    """Subscription a: blocked, with an account and both limit windows; b: plain, no limits yet."""
    stack = wire_stack()
    stack.usage.limits = Limits(
        five_hour=Window(12.5, 4_000_000_100.0), weekly=Window(40.0, 4_000_000_900.0)
    )
    await stack.admin.refresh_limits(ACTOR, "a")
    await stack.base.states.apply(
        stack.base.repo._subscriptions["a"],  # type: ignore[attr-defined]
        LimitExhausted(LimitWindow.WEEKLY, 4_000_000_500.0),
    )
    stack.usage.limits = Limits(weekly=Window(40.0, 4_000_000_900.0))
    return stack


def body_of(response: httpx.Response) -> object:
    return response.json() if response.content else None


# Breaks when: a field of a subscription, provider, login or refresh body is renamed, added or dropped
async def test_wire_field_lists() -> None:
    stack = await seeded_wire_stack()
    async with api_client(stack) as client:
        listed = (await client.get("/agentek/subscriptions", headers=ADMIN)).json()
        refreshed = (
            await client.post("/agentek/subscriptions/b/refresh-limits", headers=ADMIN)
        ).json()
        started = (
            await client.post(
                "/agentek/subscriptions/login/start",
                json={"provider": "chatgpt"},
                headers=ADMIN,
            )
        ).json()
        poll = {"url": "/agentek/subscriptions/login/poll", "headers": ADMIN}
        pending = (
            await client.post(json={**LOGIN_POLL, "name": "fresh"}, **poll)
        ).json()
        done = (await client.post(json={**LOGIN_POLL, "name": "fresh"}, **poll)).json()
        provider = (
            await client.put(
                "/agentek/subscriptions/providers/chatgpt",
                json={"concurrency_limit": 2},
                headers=ADMIN,
            )
        ).json()
        removed = await client.delete("/agentek/subscriptions/b", headers=ADMIN)

    blocked = listed["subscriptions"][0]
    assert {
        "overview": set(listed),
        "provider": set(listed["providers"][0]),
        "provider_put": set(provider),
        "subscription": set(blocked),
        "state": set(blocked["state"]),
        "limits": set(blocked["limits"]),
        "window": set(blocked["limits"]["weekly"]),
        "login_start": set(started),
        "login_pending": pending,
        "login_done": set(done),
        "login_done_subscription": set(done["subscription"]),
        "refresh": set(refreshed),
        "delete": (removed.status_code, removed.content),
    } == {
        "overview": {"providers", "subscriptions"},
        "provider": PROVIDER_FIELDS,
        "provider_put": PROVIDER_FIELDS,
        "subscription": SUBSCRIPTION_FIELDS,
        "state": STATE_FIELDS,
        "limits": LIMITS_FIELDS,
        "window": WINDOW_FIELDS,
        "login_start": LOGIN_START_FIELDS,
        "login_pending": {"status": "pending"},
        "login_done": {"status", "subscription"},
        "login_done_subscription": SUBSCRIPTION_FIELDS,
        "refresh": REFRESH_FIELDS,
        "delete": (204, b""),
    }


# Breaks when: the router answers a call the console sends differently from what the console client was recorded to read
async def test_replayed_console_calls() -> None:
    path = golden_file(GOLDEN_NAME)
    golden = json.loads(path.read_text())
    stack = await seeded_wire_stack()

    answers = []
    async with api_client(stack) as client:
        for call in golden["calls"]:
            request = call["request"]
            response = await client.request(
                request["method"],
                request["path"],
                json=request.get("body"),
                headers={**ADMIN, golden["actorHeader"]: golden["actor"]},
            )
            answers.append(
                {"status": response.status_code, "response": body_of(response)}
            )

    audited = {entry.actor for entry in stack.base.audit.entries}
    states = [state.value for state in SubscriptionState]

    if os.environ.get(UPDATE_ENV):
        for call, answer in zip(golden["calls"], answers, strict=True):
            call["status"] = answer["status"]
            call.pop("response", None)
            if answer["response"] is not None:
                call["response"] = answer["response"]
        golden["states"] = states
        path.write_text(json.dumps(golden, indent=2, ensure_ascii=False) + "\n")
        return

    assert [
        (call["name"], call["status"], call.get("response")) for call in golden["calls"]
    ] == [
        (call["name"], answer["status"], answer["response"])
        for call, answer in zip(golden["calls"], answers, strict=True)
    ]
    assert golden["states"] == states
    assert golden["actorHeader"] == ACTOR_HEADER
    assert (golden["actor"], audited) == (ACTOR, {ACTOR})
