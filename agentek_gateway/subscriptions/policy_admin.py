from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from .admin import InvalidRequestError, NotFoundError
from .audit import AuditEntry, AuditLog, PrismaAuditLog
from .model import Subscription, SubscriptionId
from .policy import (
    PolicyError,
    Subject,
    SubscriptionPolicy,
    Visibility,
    VisibilityKind,
    parse_subject,
    subject_token,
    validate_subscription_policy,
)
from .policy_cache import PolicyVersions
from .ports import PolicyRepo, SubscriptionRepo
from .unit import TransactionalDb

ACTION_PREFIX = "subscription."


@dataclass(frozen=True, slots=True)
class PolicyChange:
    """Fields left as None stay as they are."""

    visibility: Visibility | None = None
    bound: frozenset[Subject] | None = None


@dataclass(frozen=True, slots=True)
class PolicyView:
    subscription_id: SubscriptionId
    name: str
    visibility: Visibility
    bound: frozenset[Subject]

    def as_json(self) -> dict[str, object]:
        return {
            "subscription_id": self.subscription_id,
            "name": self.name,
            "visibility": {
                "kind": self.visibility.kind.value,
                "subjects": _tokens(self.visibility.subjects),
            },
            "bound_subjects": _tokens(self.bound),
        }


class PolicyWriter(Protocol):
    async def read(self, subscription_id: SubscriptionId) -> SubscriptionPolicy: ...

    async def write(
        self, subscription_id: SubscriptionId, policy: SubscriptionPolicy
    ) -> None: ...


class PolicyWrites(Protocol):
    @property
    def policy(self) -> PolicyWriter: ...

    @property
    def audit(self) -> AuditLog: ...


PolicyUnit = Callable[[], AbstractAsyncContextManager[PolicyWrites]]


@dataclass(frozen=True, slots=True)
class PolicyWritesOf:
    policy: PolicyWriter
    audit: AuditLog


class PolicyRowTable(Protocol):
    async def find_unique(self, *, where: Mapping[str, str]) -> object | None: ...

    async def upsert(
        self, *, where: Mapping[str, str], data: Mapping[str, object]
    ) -> object: ...


class PrismaPolicyWriter:
    def __init__(self, table: Callable[[], PolicyRowTable]) -> None:
        self._table = table

    async def read(self, subscription_id: SubscriptionId) -> SubscriptionPolicy:
        row = await self._table().find_unique(
            where={"subscription_id": subscription_id}
        )
        if row is None:
            return SubscriptionPolicy()
        return SubscriptionPolicy(
            Visibility(
                VisibilityKind(row.visibility),  # type: ignore[attr-defined]
                frozenset(parse_subject(t) for t in row.visibility_subjects),  # type: ignore[attr-defined]
            ),
            frozenset(parse_subject(t) for t in row.bound_subjects),  # type: ignore[attr-defined]
        )

    async def write(
        self, subscription_id: SubscriptionId, policy: SubscriptionPolicy
    ) -> None:
        fields = {
            "visibility": policy.visibility.kind.value,
            "visibility_subjects": _tokens(policy.visibility.subjects),
            "bound_subjects": _tokens(policy.bound),
        }
        await self._table().upsert(
            where={"subscription_id": subscription_id},
            data={
                "create": {"subscription_id": subscription_id, **fields, "version": 1},
                "update": {**fields, "version": {"increment": 1}},
            },
        )


def fixed_policy_unit(policy: PolicyWriter, audit: AuditLog) -> PolicyUnit:
    @asynccontextmanager
    async def unit() -> AsyncIterator[PolicyWrites]:
        yield PolicyWritesOf(policy, audit)

    return unit


class PrismaPolicyUnit:
    """The policy row and its audit entries commit together."""

    def __init__(self, db: Callable[[], TransactionalDb]) -> None:
        self._db = db

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[PolicyWrites]:
        async with self._db().tx() as tx:
            yield PolicyWritesOf(
                PrismaPolicyWriter(lambda: tx.litellm_agenteksubscriptionpolicy),  # type: ignore[attr-defined]
                PrismaAuditLog(lambda: tx.litellm_agentekaudit),  # type: ignore[attr-defined]
            )


@dataclass(frozen=True, slots=True)
class PolicyDeps:
    repo: SubscriptionRepo
    policy: PolicyRepo
    unit: PolicyUnit
    versions: PolicyVersions
    on_changed: Callable[[], None]
    announce: Callable[[], Awaitable[None]]


class PolicyAdmin:
    """Who may use which subscription. Every change is audited and reaches all replicas without a restart."""

    def __init__(self, deps: PolicyDeps) -> None:
        self._deps = deps

    async def overview(self) -> list[PolicyView]:
        deps = self._deps
        policy = await deps.policy.load_policy()
        return [
            PolicyView(
                sub.id,
                sub.name,
                policy.visibility.get(sub.id, Visibility()),
                policy.bindings.get(sub.id, frozenset()),
            )
            for sub in await deps.repo.list_subscriptions()
        ]

    async def update(
        self, actor: str, subscription_id: SubscriptionId, change: PolicyChange
    ) -> PolicyView:
        deps = self._deps
        subscription = await self._find(subscription_id)
        if change.visibility is None and change.bound is None:
            raise InvalidRequestError("nothing to change")
        async with deps.unit() as writes:
            before = await writes.policy.read(subscription_id)
            after = SubscriptionPolicy(
                change.visibility or before.visibility,
                before.bound if change.bound is None else change.bound,
            )
            try:
                validate_subscription_policy(after)
            except PolicyError as error:
                raise InvalidRequestError(str(error)) from error
            if after != before:
                await writes.policy.write(subscription_id, after)
                for entry in _entries(actor, subscription, before, after):
                    await writes.audit.record(entry)
        if after != before:
            await deps.versions.bump()
            deps.on_changed()
            await deps.announce()
        return PolicyView(
            subscription.id, subscription.name, after.visibility, after.bound
        )

    async def _find(self, subscription_id: SubscriptionId) -> Subscription:
        for subscription in await self._deps.repo.list_subscriptions():
            if subscription.id == subscription_id:
                return subscription
        raise NotFoundError(f"subscription {subscription_id} not found")


def _entries(
    actor: str,
    subscription: Subscription,
    before: SubscriptionPolicy,
    after: SubscriptionPolicy,
) -> Sequence[AuditEntry]:
    entries: list[AuditEntry] = []
    if before.visibility != after.visibility:
        entries.append(
            AuditEntry(
                actor=actor,
                action=f"{ACTION_PREFIX}visibility",
                subscription_id=subscription.id,
                subscription_name=subscription.name,
                before=_visibility_json(before.visibility),
                after=_visibility_json(after.visibility),
            )
        )
    for subject in sorted(before.bound - after.bound, key=subject_token):
        entries.append(_binding_entry(actor, subscription, subject, bound=False))
    for subject in sorted(after.bound - before.bound, key=subject_token):
        entries.append(_binding_entry(actor, subscription, subject, bound=True))
    return entries


def _binding_entry(
    actor: str, subscription: Subscription, subject: Subject, *, bound: bool
) -> AuditEntry:
    return AuditEntry(
        actor=actor,
        action=f"{ACTION_PREFIX}binding",
        subscription_id=subscription.id,
        subscription_name=subscription.name,
        subject=subject_token(subject),
        before={"bound": not bound},
        after={"bound": bound},
    )


def _visibility_json(visibility: Visibility) -> dict[str, object]:
    return {"kind": visibility.kind.value, "subjects": _tokens(visibility.subjects)}


def _tokens(subjects: frozenset[Subject]) -> list[str]:
    return sorted(subject_token(subject) for subject in subjects)


@dataclass(slots=True)
class PolicyAdminSlot:
    admin: PolicyAdmin | None = None


POLICY_ADMIN_SLOT = PolicyAdminSlot()
