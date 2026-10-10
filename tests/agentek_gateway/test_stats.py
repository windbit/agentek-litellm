from dataclasses import replace
from datetime import date, datetime, timezone


from agentek_gateway.subscriptions.admin import SubscriptionAdmin
from agentek_gateway.subscriptions.failures import SwitchReason
from agentek_gateway.subscriptions.policy import Policy
from agentek_gateway.subscriptions.selection import Snapshot
from agentek_gateway.subscriptions.stats import (
    PrismaStatsStore,
    StateTimeCredit,
    StatsBuffer,
    StatsLoop,
    StatsTelemetry,
)
from agentek_gateway.subscriptions.stats_report import (
    PrismaStatsReader,
    SpendRow,
    build_report,
)
from agentek_gateway.subscriptions.telemetry import NullTelemetry

from .conftest import FakeClock, make_subscription
from .live import live_db, needs_postgres
from .test_admin_api import ADMIN, api_client, seeded

TODAY = date(2026, 10, 10)
SUB_A, SUB_B = make_subscription("a"), make_subscription("b")


class FixedSnapshot:
    def __init__(self, *subscriptions) -> None:  # type: ignore[no-untyped-def]
        self.current = Snapshot(
            subscriptions={sub.id: sub for sub in subscriptions},
            states={},
            usage={},
            in_flight={},
            policy=Policy(),
            unsupported=frozenset(),
            subscription_models=frozenset(),
        )


class RecordingStore:
    def __init__(self, fail: bool = False) -> None:
        self.added: list[dict] = []  # type: ignore[type-arg]
        self.fail = fail

    async def add(self, deltas):  # type: ignore[no-untyped-def]
        if self.fail:
            return dict(deltas)
        self.added.append(dict(deltas))
        return {}


def day_of(clock: FakeClock) -> date:
    return datetime.fromtimestamp(clock.now(), timezone.utc).date()


def test_failures_and_switches_are_counted_per_reason(clock: FakeClock) -> None:
    buffer = StatsBuffer(clock)
    telemetry = StatsTelemetry(NullTelemetry(), buffer)

    telemetry.failed(SUB_A, SwitchReason.LIMIT)
    telemetry.failed(SUB_A, SwitchReason.LIMIT)
    telemetry.switched(SUB_A, SwitchReason.LIMIT)
    telemetry.switched(SUB_A, SwitchReason.BUSY)

    delta = buffer.take()[("a", day_of(clock))]
    assert (dict(delta.failures), dict(delta.switches)) == (
        {"limit": 2},
        {"limit": 1, "busy": 1},
    )


async def test_time_is_credited_since_the_previous_tick_and_restarts_after_a_lost_lease(
    clock: FakeClock,
) -> None:
    buffer = StatsBuffer(clock)
    snapshot = FixedSnapshot(SUB_A, replace(SUB_B, enabled=False))
    credit = StateTimeCredit(snapshot, buffer, clock)  # type: ignore[arg-type]

    await credit.tick()
    clock.advance(5)
    await credit.tick()
    clock.advance(3600)
    credit.lost_lease()
    await credit.tick()
    clock.advance(7)
    await credit.tick()

    taken = buffer.take()
    assert dict(taken[("a", day_of(clock))].state_seconds) == {"ACTIVE": 12.0}
    assert dict(taken[("b", day_of(clock))].state_seconds) == {"DISABLED": 12.0}


def test_counts_after_midnight_go_to_the_next_day(clock: FakeClock) -> None:
    buffer = StatsBuffer(clock)
    clock.current = datetime(2026, 10, 10, 23, 59, 59, tzinfo=timezone.utc).timestamp()
    buffer.failed(SUB_A, SwitchReason.LIMIT)
    clock.advance(2)
    buffer.failed(SUB_A, SwitchReason.LIMIT)

    assert sorted(
        (key[1], dict(delta.failures)) for key, delta in buffer.take().items()
    ) == [
        (date(2026, 10, 10), {"limit": 1}),
        (date(2026, 10, 11), {"limit": 1}),
    ]


async def test_a_row_that_failed_to_write_is_kept_for_the_next_flush(
    clock: FakeClock,
) -> None:
    buffer = StatsBuffer(clock)
    buffer.failed(SUB_A, SwitchReason.AUTH)
    snapshot = FixedSnapshot(SUB_A)
    failing = RecordingStore(fail=True)

    await StatsLoop(buffer, failing, snapshot, clock).flush()  # type: ignore[arg-type]
    healthy = RecordingStore()
    await StatsLoop(buffer, healthy, snapshot, clock).flush()  # type: ignore[arg-type]

    assert dict(healthy.added[0][("a", day_of(clock))].failures) == {"auth": 1}


async def test_rows_of_removed_subscriptions_are_dropped(clock: FakeClock) -> None:
    buffer = StatsBuffer(clock)
    buffer.failed(SUB_B, SwitchReason.AUTH)
    store = RecordingStore()

    await StatsLoop(buffer, store, FixedSnapshot(SUB_A), clock).flush()  # type: ignore[arg-type]

    assert store.added == []


def test_report_fills_every_day_of_the_period() -> None:
    period = [date(2026, 10, 9), TODAY]

    report = build_report(period, [], [])

    assert [day.day for day in report.days] == ["2026-10-09", "2026-10-10"]


def test_top_models_are_the_five_costliest_in_descending_order_of_spend() -> None:
    rows = [
        SpendRow("2026-10-10", f"model-{index}", 1, 10, float(index))
        for index in range(7)
    ]

    report = build_report([TODAY], rows, [])

    assert [model.model for model in report.top_models] == [
        "model-6",
        "model-5",
        "model-4",
        "model-3",
        "model-2",
    ]


class ScriptedStats:
    def __init__(self) -> None:
        self.asked: list[int] = []

    async def report(self, subscriptions, days, today):  # type: ignore[no-untyped-def]
        self.asked.append(days)
        return {sub.id: build_report([today], [], []) for sub in subscriptions}


async def test_stats_route_returns_a_report_per_subscription_and_bounds_the_period() -> (
    None
):
    stack = seeded("a")
    admin = SubscriptionAdmin(replace(stack.admin._deps, stats=ScriptedStats()))
    async with api_client(stack, admin) as client:
        ok = await client.get("/agentek/subscriptions/stats?days=1", headers=ADMIN)
        bounds = [
            (
                await client.get(
                    f"/agentek/subscriptions/stats?days={days}", headers=ADMIN
                )
            ).status_code
            for days in (0, 31)
        ]

    assert (ok.status_code, list(ok.json()["subscriptions"]), bounds) == (
        200,
        ["a"],
        [422, 422],
    )


async def add_spend(db, tag: str, day: str, **fields) -> None:  # type: ignore[no-untyped-def]
    await db.litellm_dailytagspend.create(
        data={
            "tag": tag,
            "date": day,
            "api_key": "key",
            "model": "gpt-5.4",
            "custom_llm_provider": "chatgpt",
            "mcp_namespaced_tool_name": "",
            "endpoint": "/v1/responses",
            **fields,
        }
    )


@needs_postgres
async def test_switch_from_a_to_b_is_billed_to_b_and_counted_as_a_failure_of_a(
    clock: FakeClock,
) -> None:
    async with live_db() as db:
        await db.litellm_dailytagspend.delete_many()
        for sub in (SUB_A, SUB_B):
            await db.litellm_agenteksubscription.create(
                data={
                    "id": sub.id,
                    "provider": sub.provider,
                    "name": sub.name,
                    "credential_name": sub.credential_name,
                }
            )
        today = day_of(clock).isoformat()
        await add_spend(
            db,
            "Credential: cred-b",
            today,
            prompt_tokens=1000,
            completion_tokens=200,
            spend=0.5,
            successful_requests=3,
            api_requests=3,
        )
        await add_spend(
            db, "Team: other", today, prompt_tokens=9, spend=9.0, successful_requests=9
        )
        buffer = StatsBuffer(clock)
        telemetry = StatsTelemetry(NullTelemetry(), buffer)
        telemetry.failed(SUB_A, SwitchReason.LIMIT)
        telemetry.switched(SUB_A, SwitchReason.LIMIT)
        buffer.credit_state("a", "RATE_LIMITED", 120)
        store = PrismaStatsStore(lambda: db)

        assert await store.add(buffer.take()) == {}
        buffer.failed(SUB_A, SwitchReason.LIMIT)
        buffer.credit_state("a", "RATE_LIMITED", 30)
        assert await store.add(buffer.take()) == {}
        reports = await PrismaStatsReader(
            lambda: db.litellm_dailytagspend,
            lambda: db.litellm_agenteksubscriptiondailystat,
            clock,
        ).report([SUB_A, SUB_B], 3, day_of(clock))

        a, b = (reports[sub].days[-1] for sub in ("a", "b"))
        assert (a.requests, a.tokens, a.failures, a.switches, a.state_seconds) == (
            0,
            0,
            {"limit": 2},
            {"limit": 1},
            {"RATE_LIMITED": 150},
        )
        assert (b.requests, b.tokens, b.spend, b.failures) == (3, 1200, 0.5, {})
        assert [model.model for model in reports["b"].top_models] == ["gpt-5.4"]
        assert len(reports["a"].days) == 3


class CountingTables:
    def __init__(self) -> None:
        self.queries = 0

    async def group_by(self, **_: object) -> list[dict[str, object]]:
        self.queries += 1
        return []

    async def find_many(self, **_: object) -> list[object]:
        return []


async def test_a_report_is_reused_within_the_cache_time_and_loaded_again_after_it(
    clock: FakeClock,
) -> None:
    tables = CountingTables()
    reader = PrismaStatsReader(lambda: tables, lambda: tables, clock)  # type: ignore[arg-type,return-value]

    for step in (0, 10, 50):
        clock.advance(step)
        await reader.report([SUB_A], 3, TODAY)

    assert tables.queries == 2


async def test_the_subscription_list_shows_requests_in_flight() -> None:
    stack = seeded("a")
    assert await stack.admin._deps.slots.reserve("a", "request-1", None, 60.0)
    async with api_client(stack) as client:
        listed = await client.get("/agentek/subscriptions", headers=ADMIN)

    assert [item["in_flight"] for item in listed.json()["subscriptions"]] == [1]
