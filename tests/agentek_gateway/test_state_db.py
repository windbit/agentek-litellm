from datetime import datetime, timezone
from types import SimpleNamespace

from agentek_gateway.subscriptions.model import (
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState as S,
)
from agentek_gateway.subscriptions.state_db import PrismaStateDb

NOW = 1_800_000_000.0


class FakeTable:
    """Behaves like the generated Prisma delegate for the calls PrismaStateDb makes."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    async def find_many(self):  # type: ignore[no-untyped-def]
        return list(self.rows.values())

    async def find_unique(self, *, where):  # type: ignore[no-untyped-def]
        return self.rows.get(where["subscription_id"])

    async def upsert(self, *, where, data):  # type: ignore[no-untyped-def]
        key = where["subscription_id"]
        if key in self.rows:
            vars(self.rows[key]).update(data["update"])
        else:
            self.rows[key] = SimpleNamespace(
                updated_at=datetime.fromtimestamp(NOW, timezone.utc), **data["create"]
            )

    async def delete_many(self, *, where):  # type: ignore[no-untyped-def]
        self.rows.pop(where["subscription_id"], None)


def record(state: S, until: float | None = None, streak: int = 0) -> StateRecord:
    return StateRecord(
        state,
        4,
        NOW,
        until,
        StateReason.LIMIT_EXHAUSTED,
        SignalSource.PROVIDER_RESPONSE,
        streak,
    )


async def test_record_is_written_with_its_columns_and_read_back() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]

    await db.write_state("a", record(S.RATE_LIMITED, NOW + 600, streak=2))
    read = await db.read_state("a")

    row = table.rows["a"]
    assert (row.state, row.overloaded_hits, row.version, row.reason) == (
        "RATE_LIMITED",
        2,
        4,
        "limit_exhausted",
    )
    assert read == record(S.RATE_LIMITED, NOW + 600, streak=2)


async def test_second_write_updates_the_same_row() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]
    await db.write_state("a", record(S.RATE_LIMITED, NOW + 600))

    await db.write_state("a", record(S.BANNED))

    assert (len(table.rows), table.rows["a"].state, table.rows["a"].until) == (
        1,
        "BANNED",
        None,
    )


async def test_all_rows_are_listed_and_a_row_can_be_deleted() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]
    await db.write_state("a", record(S.BANNED))
    await db.write_state("b", record(S.BROKEN))

    await db.delete_state("a")

    assert sorted(await db.read_all_states()) == ["b"]


async def test_unknown_reason_in_a_row_degrades_instead_of_failing() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]
    await db.write_state("a", record(S.BANNED))
    table.rows["a"].reason = "invented_by_a_newer_version"

    read = await db.read_state("a")

    assert read is not None and read.reason is StateReason.NONE
