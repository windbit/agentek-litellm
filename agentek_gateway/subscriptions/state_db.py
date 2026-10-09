from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .model import (
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionId,
    SubscriptionState,
)


class StateRow(Protocol):
    subscription_id: str
    state: str
    until: datetime | None
    reason: str | None
    source: str | None
    overloaded_hits: int
    version: int
    updated_at: datetime


class StateTable(Protocol):
    """The slice of the generated Prisma delegate the repository uses."""

    async def find_many(self) -> Sequence[StateRow]: ...

    async def find_unique(self, *, where: Mapping[str, str]) -> StateRow | None: ...

    async def upsert(
        self,
        *,
        where: Mapping[str, str],
        data: Mapping[str, Mapping[str, object]],
    ) -> object: ...

    async def delete_many(self, *, where: Mapping[str, str]) -> object: ...


class InMemoryStateDb:
    def __init__(self) -> None:
        self.rows: dict[SubscriptionId, StateRecord] = {}
        self.writes = 0

    async def read_state(self, subscription_id: SubscriptionId) -> StateRecord | None:
        return self.rows.get(subscription_id)

    async def read_all_states(self) -> Mapping[SubscriptionId, StateRecord]:
        return dict(self.rows)

    async def write_state(
        self, subscription_id: SubscriptionId, record: StateRecord
    ) -> None:
        self.writes += 1
        self.rows[subscription_id] = record

    async def delete_state(self, subscription_id: SubscriptionId) -> None:
        self.rows.pop(subscription_id, None)


class PrismaStateDb:
    """LiteLLM_AgentekSubscriptionState rows; entered_at is not a column, updated_at stands in for it."""

    def __init__(self, table: Callable[[], StateTable]) -> None:
        self._table = table

    async def read_state(self, subscription_id: SubscriptionId) -> StateRecord | None:
        row = await self._table().find_unique(
            where={"subscription_id": subscription_id}
        )
        return row_to_record(row) if row else None

    async def read_all_states(self) -> Mapping[SubscriptionId, StateRecord]:
        rows = await self._table().find_many()
        return {row.subscription_id: row_to_record(row) for row in rows}

    async def write_state(
        self, subscription_id: SubscriptionId, record: StateRecord
    ) -> None:
        columns: dict[str, object] = {
            "state": record.state.value,
            "until": _datetime(record.until),
            "reason": record.reason.value,
            "source": record.source.value,
            "overloaded_hits": record.overload_streak,
            "version": record.version,
        }
        await self._table().upsert(
            where={"subscription_id": subscription_id},
            data={
                "create": {"subscription_id": subscription_id, **columns},
                "update": columns,
            },
        )

    async def delete_state(self, subscription_id: SubscriptionId) -> None:
        await self._table().delete_many(where={"subscription_id": subscription_id})


def row_to_record(row: StateRow) -> StateRecord:
    return StateRecord(
        state=SubscriptionState(row.state),
        version=row.version,
        entered_at=row.updated_at.timestamp(),
        until=row.until.timestamp() if row.until else None,
        reason=_reason(row.reason),
        source=_source(row.source),
        overload_streak=row.overloaded_hits,
    )


def _datetime(timestamp: float | None) -> datetime | None:
    return (
        None if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc)
    )


def _reason(value: str | None) -> StateReason:
    try:
        return StateReason(value) if value else StateReason.NONE
    except ValueError:
        verbose_proxy_logger.warning("agentek_gateway unknown state reason %r", value)
        return StateReason.NONE


def _source(value: str | None) -> SignalSource:
    try:
        return SignalSource(value) if value else SignalSource.NONE
    except ValueError:
        verbose_proxy_logger.warning("agentek_gateway unknown signal source %r", value)
        return SignalSource.NONE
