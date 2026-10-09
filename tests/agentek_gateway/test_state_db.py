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

    def _matches(self, row, where):  # type: ignore[no-untyped-def]
        for column, condition in where.items():
            value = getattr(row, column)
            if isinstance(condition, dict):
                if "lt" in condition and not value < condition["lt"]:
                    return False
                if "lte" in condition and not value <= condition["lte"]:
                    return False
                if "in" in condition and value not in condition["in"]:
                    return False
            elif value != condition:
                return False
        return True

    async def find_many(self, *, where=None):  # type: ignore[no-untyped-def]
        return [row for row in self.rows.values() if self._matches(row, where or {})]

    async def find_unique(self, *, where):  # type: ignore[no-untyped-def]
        return self.rows.get(where["subscription_id"])

    async def create(self, *, data):  # type: ignore[no-untyped-def]
        self.rows[data["subscription_id"]] = SimpleNamespace(
            updated_at=datetime.fromtimestamp(NOW, timezone.utc), **data
        )

    async def update_many(self, *, where, data):  # type: ignore[no-untyped-def]
        changed = [row for row in self.rows.values() if self._matches(row, where)]
        for row in changed:
            vars(row).update(data)
        return len(changed)

    async def delete_many(self, *, where):  # type: ignore[no-untyped-def]
        for row in [row for row in self.rows.values() if self._matches(row, where)]:
            del self.rows[row.subscription_id]


def record(
    state: S, until: float | None = None, streak: int = 0, version: int = 4
) -> StateRecord:
    return StateRecord(
        state,
        version,
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

    await db.write_state("a", record(S.BANNED, version=5))

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

    await db.delete_state("a", 4)

    assert sorted(await db.read_all_states()) == ["b"]


async def test_unknown_reason_in_a_row_degrades_instead_of_failing() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]
    await db.write_state("a", record(S.BANNED))
    table.rows["a"].reason = "invented_by_a_newer_version"

    read = await db.read_state("a")

    assert read is not None and read.reason is StateReason.NONE


async def test_write_older_than_the_stored_row_is_ignored() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]
    await db.write_state("a", record(S.BANNED, version=7))

    await db.write_state("a", record(S.RATE_LIMITED, NOW + 60, version=6))
    await db.write_state("a", record(S.RATE_LIMITED, NOW + 60, version=7))

    assert (table.rows["a"].state, table.rows["a"].version) == ("BANNED", 7)


async def test_delete_removes_only_a_row_that_is_not_newer() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]
    await db.write_state("a", record(S.BANNED, version=7))
    await db.write_state("b", record(S.BANNED, version=3))

    await db.delete_state("a", 6)
    await db.delete_state("b", 3)

    assert sorted(table.rows) == ["a"]


async def test_several_rows_are_read_by_id() -> None:
    table = FakeTable()
    db = PrismaStateDb(lambda: table)  # type: ignore[arg-type,return-value]
    for sub_id in ("a", "b", "c"):
        await db.write_state(sub_id, record(S.BANNED))

    rows = await db.read_states(["a", "c", "missing"])

    assert sorted(rows) == ["a", "c"]
