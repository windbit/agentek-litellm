from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

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
