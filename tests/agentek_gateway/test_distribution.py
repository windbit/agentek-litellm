"""Distribution of subscriptions: policy rules, shared version cache, operator API, change in the middle of a chat."""

import random

import fakeredis
import httpx
import pytest
from fastapi import FastAPI, Request

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth

from agentek_gateway.api import build_api_router
from agentek_gateway.subscriptions.admin import AdminSlot
from agentek_gateway.subscriptions.audit import InMemoryAuditLog
from agentek_gateway.subscriptions.memory import (
    InMemoryPolicyBook,
    InMemorySubscriptionRepo,
)
from agentek_gateway.subscriptions.policy import (
    KeySubjects,
    Policy,
    PolicyError,
    Subject,
    SubscriptionPolicy,
    Visibility,
    VisibilityKind,
    eligible_tiers,
    parse_subject,
    validate_subscription_policy,
)
from agentek_gateway.subscriptions.policy_admin import (
    PolicyAdmin,
    PolicyAdminSlot,
    PolicyDeps,
    fixed_policy_unit,
)
from agentek_gateway.subscriptions.policy_cache import (
    CachedPolicyRepo,
    RedisPolicyVersions,
)
from agentek_gateway.subscriptions.selection import Kept, SelectionRequest, select

from .builders import MODEL, NOW, deployments_for, snapshot_of, subject
from .conftest import FakeClock, make_subscription
from .stack import account_of, running_stack

ALL, ALL_EXCEPT, ONLY = (
    VisibilityKind.ALL,
    VisibilityKind.ALL_EXCEPT,
    VisibilityKind.ONLY,
)
E1, E2 = subject("employee", "e1"), subject("employee", "e2")
S1, S2 = subject("space", "s1"), subject("space", "s2")
SVC = subject("service", "kb")
PEOPLE = [E1, E2, S1, S2, SVC]
OPERATOR = "operator-1"
ADMIN = {"authorization": "admin", "x-agentek-actor": "boxadmin@example.test"}


def policy_of(
    visibility: Visibility = Visibility(), *bound: Subject
) -> SubscriptionPolicy:
    return SubscriptionPolicy(visibility, frozenset(bound))


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        (policy_of(Visibility(ONLY)), "at least one subject"),
        (policy_of(Visibility(ALL, frozenset({E1}))), "takes no subjects"),
        (
            policy_of(Visibility(ONLY, frozenset({E1})), S1),
            "contradicts visibility 'only'",
        ),
        (
            policy_of(Visibility(ALL_EXCEPT, frozenset({S1})), S1),
            "contradicts visibility 'all_except'",
        ),
    ],
)
def test_a_contradicting_or_empty_policy_is_refused_with_a_reason(policy, reason) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PolicyError, match=reason):
        validate_subscription_policy(policy)


@pytest.mark.parametrize(
    "policy",
    [
        policy_of(),
        policy_of(Visibility(), S1, E1),
        policy_of(Visibility(ONLY, frozenset({S1, E1})), S1),
        policy_of(Visibility(ALL_EXCEPT, frozenset({E2})), S1),
        policy_of(Visibility(ALL_EXCEPT)),
    ],
)
def test_a_consistent_policy_is_accepted(policy) -> None:  # type: ignore[no-untyped-def]
    validate_subscription_policy(policy)


@pytest.mark.parametrize(
    "token", ["space", "space:", "team:1", "space:a b", ":1", "space:" + "x" * 65]
)
def test_a_malformed_subject_is_refused(token: str) -> None:
    with pytest.raises(PolicyError):
        parse_subject(token)


def test_binding_does_not_open_a_subscription_to_a_key_its_visibility_excludes() -> (
    None
):
    policy = Policy(
        visibility={"d": Visibility(ALL_EXCEPT, frozenset({E1}))},
        bindings={"d": frozenset({S1})},
    )

    own, shared = eligible_tiers(policy, frozenset({"d"}), KeySubjects(E1, S1))

    assert (own, shared) == (frozenset(), frozenset())


def test_the_narrowest_binding_is_not_bypassed_when_it_is_closed_to_the_key() -> None:
    policy = Policy(
        visibility={"d2": Visibility(ALL_EXCEPT, frozenset({E1}))},
        bindings={"d1": frozenset({E1}), "d2": frozenset({S1})},
    )

    own, _ = eligible_tiers(policy, frozenset({"d1", "d2"}), KeySubjects(E1, S1))

    assert own == frozenset()


def random_policy(rng: random.Random, ids: list[str]) -> Policy:
    visibility, bindings = {}, {}
    for sub_id in ids:
        candidate = SubscriptionPolicy(
            Visibility(
                rng.choice(list(VisibilityKind)),
                frozenset(rng.sample(PEOPLE, rng.randint(0, 3))),
            ),
            frozenset(rng.sample(PEOPLE, rng.randint(0, 2))),
        )
        try:
            validate_subscription_policy(candidate)
        except PolicyError:
            continue
        visibility[sub_id] = candidate.visibility
        if candidate.bound:
            bindings[sub_id] = candidate.bound
    return Policy(visibility=visibility, bindings=bindings)


def test_property_a_chosen_subscription_is_visible_and_never_bound_to_strangers() -> (
    None
):
    rng = random.Random(20261010)
    keys = [
        None,
        KeySubjects(E1),
        KeySubjects(E2, S1),
        KeySubjects(E1, S2, SVC),
        KeySubjects(space=S1),
    ]
    for _ in range(600):
        ids = [f"s{index}" for index in range(rng.randint(1, 6))]
        policy = random_policy(rng, ids)
        subjects = rng.choice(keys)
        snapshot = snapshot_of(
            [make_subscription(sub_id) for sub_id in ids], policy=policy
        )

        outcome = select(
            SelectionRequest(MODEL, subjects), deployments_for(*ids), snapshot, NOW
        )

        if not isinstance(outcome, Kept) or outcome.chosen is None:
            continue
        chosen = outcome.chosen.subscription_id
        bound = policy.bindings.get(chosen, frozenset())
        visibility = policy.visibility.get(chosen, Visibility())
        if subjects is None:
            assert not bound and visibility.kind is ALL
            continue
        assert not bound or bound & subjects.all
        assert visibility.kind is not ONLY or visibility.subjects & subjects.all
        assert (
            visibility.kind is not ALL_EXCEPT or not visibility.subjects & subjects.all
        )


# shared version cache


async def test_policy_is_reloaded_only_when_the_shared_version_moves() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    versions = RedisPolicyVersions(redis, "t:policy-version")
    book = InMemoryPolicyBook()
    cache = CachedPolicyRepo(book, versions, FakeClock())

    await cache.load_policy()
    await cache.load_policy()
    unchanged_loads = book.loads
    await versions.bump()
    reloaded = await cache.load_policy()

    assert (unchanged_loads, book.loads, reloaded.version) == (1, 2, 1)


async def test_a_second_replica_sees_a_change_after_the_version_bump() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    versions = RedisPolicyVersions(redis, "t:policy-version")
    book = InMemoryPolicyBook()
    clock = FakeClock()
    replica = CachedPolicyRepo(book, versions, clock)
    await replica.load_policy()

    await book.write("a", policy_of(Visibility(ONLY, frozenset({S1}))))
    stale = await replica.load_policy()
    await versions.bump()
    fresh = await replica.load_policy()

    assert (stale.visibility, "a" in fresh.visibility) == ({}, True)


async def test_a_lost_counter_does_not_keep_a_stale_policy_longer_than_the_max_age() -> (
    None
):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    versions = RedisPolicyVersions(redis, "t:policy-version")
    book = InMemoryPolicyBook()
    clock = FakeClock()
    replica = CachedPolicyRepo(book, versions, clock, max_age_s=30.0)
    await replica.load_policy()
    await book.write("a", policy_of(Visibility(ONLY, frozenset({S1}))))

    clock.advance(31.0)

    assert "a" in (await replica.load_policy()).visibility


async def test_an_unreadable_version_falls_back_to_the_database() -> None:
    class Broken:
        async def current(self) -> int:
            raise ConnectionError("redis down")

        async def bump(self) -> None:
            raise ConnectionError("redis down")

    book = InMemoryPolicyBook()
    cache = CachedPolicyRepo(book, Broken(), FakeClock())

    await cache.load_policy()
    await book.write("a", policy_of(Visibility(ONLY, frozenset({S1}))))

    assert "a" in (await cache.load_policy()).visibility


# operator API


class World:
    def __init__(self, *sub_ids: str) -> None:
        self.repo = InMemorySubscriptionRepo([make_subscription(i) for i in sub_ids])
        self.book = InMemoryPolicyBook()
        self.audit = InMemoryAuditLog()
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.versions = RedisPolicyVersions(self.redis, "t:policy-version")
        self.changed = 0
        self.announced = 0
        self.admin = PolicyAdmin(
            PolicyDeps(
                repo=self.repo,
                policy=self.book,
                unit=fixed_policy_unit(self.book, self.audit),
                versions=self.versions,
                on_changed=self._changed,
                announce=self._announce,
            )
        )

    def _changed(self) -> None:
        self.changed += 1

    async def _announce(self) -> None:
        self.announced += 1

    def client(self) -> httpx.AsyncClient:
        app = FastAPI()
        app.include_router(
            build_api_router(AdminSlot(None), PolicyAdminSlot(self.admin))
        )

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


async def test_saving_a_binding_is_applied_audited_and_announced() -> None:
    world = World("a")
    async with world.client() as client:
        response = await client.put(
            "/agentek/subscriptions/a/policy",
            headers=ADMIN,
            json={"bound_subjects": ["space:s1"]},
        )

    entry = world.audit.entries[0]
    assert (
        response.status_code,
        world.book.rows["a"].bound,
        (
            entry.actor,
            entry.action,
            entry.subscription_id,
            entry.subject,
            entry.before,
            entry.after,
        ),
        await world.versions.current(),
        (world.changed, world.announced),
    ) == (
        200,
        frozenset({S1}),
        (
            "boxadmin@example.test",
            "subscription.binding",
            "a",
            "space:s1",
            {"bound": False},
            {"bound": True},
        ),
        1,
        (1, 1),
    )


async def test_removing_a_binding_records_who_what_before_and_after() -> None:
    world = World("a")
    world.book.rows["a"] = policy_of(Visibility(), S1)
    async with world.client() as client:
        await client.put(
            "/agentek/subscriptions/a/policy",
            headers=ADMIN,
            json={"bound_subjects": []},
        )

    entry = world.audit.entries[0]
    assert (entry.actor, entry.subject, entry.before, entry.after) == (
        "boxadmin@example.test",
        "space:s1",
        {"bound": True},
        {"bound": False},
    )


async def test_visibility_change_is_audited_with_old_and_new_value() -> None:
    world = World("a")
    async with world.client() as client:
        await client.put(
            "/agentek/subscriptions/a/policy",
            headers=ADMIN,
            json={"visibility": {"kind": "all_except", "subjects": ["employee:e1"]}},
        )

    entry = world.audit.entries[0]
    assert (entry.action, entry.before, entry.after) == (
        "subscription.visibility",
        {"kind": "all", "subjects": []},
        {"kind": "all_except", "subjects": ["employee:e1"]},
    )


@pytest.mark.parametrize(
    "body",
    [
        {"visibility": {"kind": "only", "subjects": []}},
        {
            "visibility": {"kind": "only", "subjects": ["employee:e1"]},
            "bound_subjects": ["space:s1"],
        },
        {
            "visibility": {"kind": "all_except", "subjects": ["space:s1"]},
            "bound_subjects": ["space:s1"],
        },
        {"bound_subjects": ["team:1"]},
        {},
    ],
)
async def test_an_invalid_policy_is_refused_and_leaves_no_trace(body: object) -> None:
    world = World("a")
    async with world.client() as client:
        response = await client.put(
            "/agentek/subscriptions/a/policy", headers=ADMIN, json=body
        )

    assert (
        response.status_code,
        world.book.rows,
        world.audit.entries,
        await world.versions.current(),
    ) == (
        422,
        {},
        [],
        0,
    )


async def test_binding_that_contradicts_the_stored_visibility_is_refused() -> None:
    world = World("a")
    world.book.rows["a"] = policy_of(Visibility(ALL_EXCEPT, frozenset({S1})))
    async with world.client() as client:
        response = await client.put(
            "/agentek/subscriptions/a/policy",
            headers=ADMIN,
            json={"bound_subjects": ["space:s1"]},
        )

    assert (response.status_code, world.book.rows["a"].bound) == (422, frozenset())


async def test_a_caller_without_the_admin_role_is_refused() -> None:
    world = World("a")
    async with world.client() as client:
        put = await client.put(
            "/agentek/subscriptions/a/policy",
            headers={"authorization": "member"},
            json={"bound_subjects": ["space:s1"]},
        )
        get = await client.get(
            "/agentek/subscriptions/policy", headers={"authorization": "member"}
        )

    assert (put.status_code, get.status_code, world.book.rows, world.audit.entries) == (
        403,
        403,
        {},
        [],
    )


async def test_an_unknown_subscription_is_404_and_the_overview_lists_every_subscription() -> (
    None
):
    world = World("a", "b")
    world.book.rows["b"] = policy_of(Visibility(ONLY, frozenset({E1})), E1)
    async with world.client() as client:
        missing = await client.put(
            "/agentek/subscriptions/zz/policy",
            headers=ADMIN,
            json={"bound_subjects": []},
        )
        overview = await client.get("/agentek/subscriptions/policy", headers=ADMIN)

    by_id = {item["subscription_id"]: item for item in overview.json()["subscriptions"]}
    assert (
        missing.status_code,
        by_id["a"]["visibility"],
        by_id["b"]["bound_subjects"],
    ) == (
        404,
        {"kind": "all", "subjects": []},
        ["employee:e1"],
    )


async def test_saving_the_same_policy_again_writes_nothing() -> None:
    world = World("a")
    world.book.rows["a"] = policy_of(Visibility(), S1)
    async with world.client() as client:
        await client.put(
            "/agentek/subscriptions/a/policy",
            headers=ADMIN,
            json={"bound_subjects": ["space:s1"]},
        )

    assert (world.audit.entries, await world.versions.current()) == ([], 0)


# a change in the middle of a chat


async def test_closing_a_subscription_to_the_space_moves_the_running_chat_to_another() -> (
    None
):
    world = World("a", "b")
    labels = {"user_api_key_metadata": {"agentek_subjects": {"space": "s1"}}}
    async with running_stack(["a", "b"], policy=world.book) as stack:
        await stack.respond(prompt_cache_key="chat-1", litellm_metadata=dict(labels))
        await stack.respond(prompt_cache_key="chat-1", litellm_metadata=dict(labels))
        before = stack.mock.accounts_served()

        async with world.client() as client:
            await client.put(
                "/agentek/subscriptions/a/policy",
                headers=ADMIN,
                json={"visibility": {"kind": "all_except", "subjects": ["space:s1"]}},
            )
        await stack.refresh()
        await stack.respond(prompt_cache_key="chat-1", litellm_metadata=dict(labels))

        assert (before, stack.mock.accounts_served()[2:]) == (
            [account_of("a"), account_of("a")],
            [account_of("b")],
        )
