import asyncio
import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Protocol

from prisma.errors import PrismaError

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .failures import SwitchReason
from .model import Subscription, SubscriptionId, effective_state
from .selection import Snapshot
from .snapshot import SnapshotCache
from .telemetry import Telemetry

FLUSH_INTERVAL_S = 60.0

COLUMNS = ("failures", "switches", "state_seconds")

UPSERT_SQL = (
    """
INSERT INTO "LiteLLM_AgentekSubscriptionDailyStat" AS t
  (subscription_id, day, failures, switches, state_seconds, updated_at)
VALUES ($1, $2::date, $3::jsonb, $4::jsonb, $5::jsonb, now() AT TIME ZONE 'utc')
ON CONFLICT (subscription_id, day) DO UPDATE SET
"""
    + ",\n".join(
        f"""  {column} = COALESCE((SELECT jsonb_object_agg(k,
      COALESCE((t.{column} ->> k)::float8, 0) + COALESCE((EXCLUDED.{column} ->> k)::float8, 0))
    FROM jsonb_object_keys(t.{column} || EXCLUDED.{column}) AS k), '{{}}'::jsonb)"""
        for column in COLUMNS
    )
    + """,
  updated_at = EXCLUDED.updated_at
"""
)

DayKey = tuple[SubscriptionId, date]


@dataclass(slots=True)
class DailyDelta:
    failures: defaultdict[str, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    switches: defaultdict[str, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    state_seconds: defaultdict[str, float] = field(
        default_factory=lambda: defaultdict(float)
    )

    def merge(self, other: "DailyDelta") -> None:
        _accumulate(self.failures, other.failures)
        _accumulate(self.switches, other.switches)
        _accumulate(self.state_seconds, other.state_seconds)


def _accumulate(target: defaultdict[str, float], source: Mapping[str, float]) -> None:
    for key, value in source.items():
        target[key] += value


class StatsStore(Protocol):
    async def add(
        self, deltas: Mapping[DayKey, DailyDelta]
    ) -> dict[DayKey, DailyDelta]:
        """Adds the counts to the stored rows; returns the rows that could not be written."""
        ...


class StatsBuffer:
    """One entry per subscription and UTC day."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._pending: dict[DayKey, DailyDelta] = defaultdict(DailyDelta)

    def failed(self, subscription: Subscription, reason: SwitchReason) -> None:
        self._today(subscription.id).failures[reason.value] += 1

    def switched(self, subscription: Subscription, reason: SwitchReason) -> None:
        self._today(subscription.id).switches[reason.value] += 1

    def credit_state(
        self, subscription_id: SubscriptionId, state: str, seconds: float
    ) -> None:
        self._today(subscription_id).state_seconds[state] += seconds

    def take(self) -> dict[DayKey, DailyDelta]:
        taken, self._pending = dict(self._pending), defaultdict(DailyDelta)
        return taken

    def restore(self, taken: Mapping[DayKey, DailyDelta]) -> None:
        for key, delta in taken.items():
            self._pending[key].merge(delta)

    def _today(self, subscription_id: SubscriptionId) -> DailyDelta:
        return self._pending[(subscription_id, utc_day(self._clock.now()))]


def utc_day(timestamp: float) -> date:
    return datetime.fromtimestamp(timestamp, timezone.utc).date()


class StatsTelemetry:
    """Feeds the daily statistics next to the metrics."""

    def __init__(self, inner: Telemetry, buffer: StatsBuffer) -> None:
        self._inner = inner
        self._buffer = buffer

    def switched(self, subscription: Subscription, reason: SwitchReason) -> None:
        self._inner.switched(subscription, reason)
        self._buffer.switched(subscription, reason)

    def failed(self, subscription: Subscription, reason: SwitchReason) -> None:
        self._inner.failed(subscription, reason)
        self._buffer.failed(subscription, reason)


class StateTimeCredit:
    """Leader duty: credits the time since the previous tick to the current state of every subscription.

    A tick after a spell without the lease only starts the count: another replica credited that time.
    """

    def __init__(
        self, snapshot: SnapshotCache, buffer: StatsBuffer, clock: Clock
    ) -> None:
        self._snapshot = snapshot
        self._buffer = buffer
        self._clock = clock
        self._last: float | None = None

    def lost_lease(self) -> None:
        self._last = None

    async def tick(self) -> None:
        now = self._clock.now()
        last, self._last = self._last, now
        snapshot = self._snapshot.current
        if last is None or snapshot is None or snapshot.closed or now <= last:
            return
        for subscription in snapshot.subscriptions.values():
            self._buffer.credit_state(
                subscription.id, _state_name(subscription, snapshot, now), now - last
            )


def _state_name(subscription: Subscription, snapshot: Snapshot, now: float) -> str:
    if not subscription.enabled:
        return "DISABLED"
    record = snapshot.states.get(subscription.id)
    return effective_state(record, now).value if record else "ACTIVE"


class RawDb(Protocol):
    async def execute_raw(self, query: str, *args: object) -> object: ...


class NullStatsStore:
    async def add(
        self, deltas: Mapping[DayKey, DailyDelta]
    ) -> dict[DayKey, DailyDelta]:
        return dict(deltas)


class PrismaStatsStore:
    def __init__(self, db: Callable[[], RawDb]) -> None:
        self._db = db

    async def add(
        self, deltas: Mapping[DayKey, DailyDelta]
    ) -> dict[DayKey, DailyDelta]:
        db = self._db()
        failed: dict[DayKey, DailyDelta] = {}
        for key, delta in deltas.items():
            try:
                await db.execute_raw(
                    UPSERT_SQL,
                    key[0],
                    key[1].isoformat(),
                    *(
                        json.dumps(counts)
                        for counts in (
                            delta.failures,
                            delta.switches,
                            delta.state_seconds,
                        )
                    ),
                )
            except PrismaError:
                verbose_proxy_logger.exception(
                    "agentek_gateway statistics row of %s was not written", key[0]
                )
                failed[key] = delta
        return failed


class StatsLoop:
    """Writes the counts of this replica once a minute and at shutdown; a failed row is kept for the next round."""

    def __init__(
        self,
        buffer: StatsBuffer,
        store: StatsStore,
        snapshot: SnapshotCache,
        clock: Clock,
    ) -> None:
        self._buffer = buffer
        self._store = store
        self._snapshot = snapshot
        self._clock = clock

    async def run(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            await self.flush()

    async def flush(self) -> None:
        taken = self._buffer.take()
        snapshot = self._snapshot.current
        if snapshot is not None:
            taken = {
                key: delta
                for key, delta in taken.items()
                if key[0] in snapshot.subscriptions
            }
        if not taken:
            return
        self._buffer.restore(await self._store.add(taken))
