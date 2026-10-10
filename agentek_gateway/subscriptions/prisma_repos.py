import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from prisma.errors import UniqueViolationError

from litellm._logging import verbose_proxy_logger

from .model import Subscription, SubscriptionId
from .policy import Policy, Subject, SubjectKind, Visibility, VisibilityKind


class SubscriptionRow(Protocol):
    id: str
    provider: str
    name: str
    credential_name: str
    priority: int
    enabled: bool
    max_concurrency: int | None
    egress: str | None


class SubscriptionTable(Protocol):
    """The slice of the generated Prisma delegate the repository uses."""

    async def find_many(self) -> Sequence[SubscriptionRow]: ...

    async def update(
        self, *, where: Mapping[str, str], data: Mapping[str, object]
    ) -> object: ...

    async def create(self, *, data: Mapping[str, object]) -> object: ...

    async def delete_many(self, *, where: Mapping[str, object]) -> int: ...


class PolicyRow(Protocol):
    subscription_id: str
    visibility: str
    visibility_subjects: Sequence[str]
    bound_subjects: Sequence[str]
    version: int


class PolicyTable(Protocol):
    async def find_many(self) -> Sequence[PolicyRow]: ...


class PrismaSubscriptionRepo:
    """Subscription records of LiteLLM_AgentekSubscription."""

    def __init__(self, table: Callable[[], SubscriptionTable]) -> None:
        self._table = table

    async def list_subscriptions(self) -> Sequence[Subscription]:
        rows = await self._table().find_many()
        return tuple(subscription_of_row(row) for row in rows)

    async def set_enabled(self, subscription_id: SubscriptionId, enabled: bool) -> None:
        await self._table().update(
            where={"id": subscription_id}, data={"enabled": enabled}
        )


DEFAULT_PRIORITY = 50
EDITABLE_FIELDS = frozenset({"priority", "max_concurrency"})


@dataclass(frozen=True, slots=True)
class NewSubscription:
    provider: str
    name: str
    credential_name: str
    priority: int = DEFAULT_PRIORITY
    enabled: bool = True


class SubscriptionWriter(Protocol):
    async def create_subscription(self, new: NewSubscription) -> Subscription | None:
        """None when a subscription with this name exists."""
        ...

    async def update_subscription(
        self, subscription_id: SubscriptionId, fields: Mapping[str, int | None]
    ) -> None: ...

    async def delete_subscription(self, subscription_id: SubscriptionId) -> None: ...


class PrismaSubscriptionWriter:
    """Writes LiteLLM_AgentekSubscription; deleting a subscription cascades to its state, policy and statistics rows."""

    def __init__(self, table: Callable[[], SubscriptionTable]) -> None:
        self._table = table

    async def create_subscription(self, new: NewSubscription) -> Subscription | None:
        subscription = Subscription(
            id=str(uuid.uuid4()),
            provider=new.provider,
            name=new.name,
            credential_name=new.credential_name,
            priority=new.priority,
            enabled=new.enabled,
        )
        try:
            await self._table().create(
                data={
                    "id": subscription.id,
                    "provider": subscription.provider,
                    "name": subscription.name,
                    "credential_name": subscription.credential_name,
                    "priority": subscription.priority,
                    "enabled": subscription.enabled,
                }
            )
        except UniqueViolationError:
            return None
        return subscription

    async def update_subscription(
        self, subscription_id: SubscriptionId, fields: Mapping[str, int | None]
    ) -> None:
        unknown = set(fields) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"not editable: {sorted(unknown)}")
        await self._table().update(where={"id": subscription_id}, data=dict(fields))

    async def delete_subscription(self, subscription_id: SubscriptionId) -> None:
        await self._table().delete_many(where={"id": subscription_id})


class PrismaPolicyRepo:
    """Visibility and bindings of LiteLLM_AgentekSubscriptionPolicy, one row per subscription."""

    def __init__(self, table: Callable[[], PolicyTable]) -> None:
        self._table = table

    async def load_policy(self) -> Policy:
        rows = await self._table().find_many()
        visibility = {row.subscription_id: _visibility_of(row) for row in rows}
        bindings = {
            row.subscription_id: _subjects(row.bound_subjects)
            for row in rows
            if row.bound_subjects
        }
        return Policy(
            visibility=visibility,
            bindings=bindings,
            version=max((row.version for row in rows), default=0),
        )


def subscription_of_row(row: SubscriptionRow) -> Subscription:
    return Subscription(
        id=row.id,
        provider=row.provider,
        name=row.name,
        credential_name=row.credential_name,
        priority=row.priority,
        concurrency_limit=row.max_concurrency,
        egress=row.egress,
        enabled=row.enabled,
    )


def _visibility_of(row: PolicyRow) -> Visibility:
    try:
        kind = VisibilityKind(row.visibility)
    except ValueError:
        verbose_proxy_logger.warning(
            "agentek_gateway unknown visibility %r of %s, treated as the narrowest",
            row.visibility,
            row.subscription_id,
        )
        return Visibility(VisibilityKind.ONLY, frozenset())
    return Visibility(kind, _subjects(row.visibility_subjects))


def _subjects(raw: Sequence[str]) -> frozenset[Subject]:
    subjects: set[Subject] = set()
    for item in raw:
        kind, _, identifier = item.partition(":")
        try:
            subjects.add(Subject(SubjectKind(kind), identifier))
        except ValueError:
            verbose_proxy_logger.warning(
                "agentek_gateway unknown subject %r ignored", item
            )
    return frozenset(subjects)
