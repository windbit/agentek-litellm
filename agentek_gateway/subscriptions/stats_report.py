from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol, cast

from .clock import Clock
from .gateway import CREDENTIAL_TAG_PREFIX
from .model import Subscription, SubscriptionId

MAX_DAYS = 30
TOP_MODELS = 5
CACHE_TTL_S = 45.0


@dataclass(frozen=True, slots=True)
class DayStats:
    day: str
    requests: int
    tokens: int
    spend: float
    failures: Mapping[str, int]
    switches: Mapping[str, int]
    state_seconds: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ModelUsage:
    model: str
    requests: int
    tokens: int
    spend: float


@dataclass(frozen=True, slots=True)
class StatsReport:
    """Oldest day first, one entry per day of the period, the last one is today."""

    days: Sequence[DayStats]
    top_models: Sequence[ModelUsage]

    def as_json(self) -> dict[str, object]:
        return asdict(self)


class StatRow(Protocol):
    subscription_id: str
    day: datetime
    failures: object
    switches: object
    state_seconds: object


class SpendTable(Protocol):
    async def group_by(
        self,
        *,
        by: Sequence[str],
        sum: Mapping[str, bool],
        where: Mapping[str, object],
    ) -> Sequence[Mapping[str, object]]: ...


class StatTable(Protocol):
    async def find_many(self, *, where: Mapping[str, object]) -> Sequence[StatRow]: ...


@dataclass(frozen=True, slots=True)
class SpendRow:
    date: str
    model: str
    requests: int
    tokens: int
    spend: float


class EmptyStats:
    async def report(
        self, subscriptions: Sequence[Subscription], days: int, today: date
    ) -> dict[SubscriptionId, StatsReport]:
        period = _period(today, days)
        return {sub.id: build_report(period, (), ()) for sub in subscriptions}


class PrismaStatsReader:
    """Spend and tokens come from LiteLLM's per-tag daily rows (tag "Credential: <name>", written for the subscription
    that served the successful attempt), summed in the database; failures, switches and time in states come from
    the plugin's daily rows. A report is reused for CACHE_TTL_S, so a dashboard left open costs one query a minute.
    """

    def __init__(
        self,
        spend: Callable[[], SpendTable],
        stats: Callable[[], StatTable],
        clock: Clock,
    ) -> None:
        self._spend = spend
        self._stats = stats
        self._clock = clock
        self._cached: (
            tuple[float, tuple[object, ...], dict[SubscriptionId, StatsReport]] | None
        ) = None

    async def report(
        self, subscriptions: Sequence[Subscription], days: int, today: date
    ) -> dict[SubscriptionId, StatsReport]:
        key = (
            days,
            today,
            tuple((sub.id, sub.credential_name) for sub in subscriptions),
        )
        now = self._clock.now()
        if (
            self._cached
            and self._cached[1] == key
            and now - self._cached[0] < CACHE_TTL_S
        ):
            return self._cached[2]
        report = await self._load(subscriptions, days, today)
        self._cached = (now, key, report)
        return report

    async def _load(
        self, subscriptions: Sequence[Subscription], days: int, today: date
    ) -> dict[SubscriptionId, StatsReport]:
        period = _period(today, days)
        tags = {
            sub.id: CREDENTIAL_TAG_PREFIX + sub.credential_name for sub in subscriptions
        }
        grouped = await self._spend().group_by(
            by=["tag", "date", "model"],
            sum={
                "prompt_tokens": True,
                "completion_tokens": True,
                "spend": True,
                "successful_requests": True,
            },
            where={
                "tag": {"in": list(tags.values())},
                "date": {"gte": period[0].isoformat()},
            },
        )
        stat_rows = await self._stats().find_many(
            where={"day": {"gte": datetime.combine(period[0], time.min)}}
        )
        spend_by_tag: dict[str, list[SpendRow]] = defaultdict(list)
        for row in grouped:
            spend_by_tag[str(row["tag"])].append(_spend_row(row))
        stats_by_id: dict[str, list[StatRow]] = defaultdict(list)
        for stat_row in stat_rows:
            stats_by_id[stat_row.subscription_id].append(stat_row)
        return {
            sub.id: build_report(
                period, spend_by_tag.get(tags[sub.id], ()), stats_by_id.get(sub.id, ())
            )
            for sub in subscriptions
        }


def _period(today: date, days: int) -> list[date]:
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _spend_row(row: Mapping[str, object]) -> SpendRow:
    sums = cast(Mapping[str, float | int | str | None], row["_sum"])
    return SpendRow(
        date=str(row["date"]),
        model=str(row["model"] or "unknown"),
        requests=int(sums["successful_requests"] or 0),
        tokens=int(sums["prompt_tokens"] or 0) + int(sums["completion_tokens"] or 0),
        spend=float(sums["spend"] or 0),
    )


def build_report(
    period: Sequence[date], spend_rows: Sequence[SpendRow], stat_rows: Sequence[StatRow]
) -> StatsReport:
    spend_of_day: dict[str, list[SpendRow]] = defaultdict(list)
    models: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for row in spend_rows:
        spend_of_day[row.date].append(row)
        total = models[row.model]
        total[0] += row.requests
        total[1] += row.tokens
        total[2] += row.spend
    stats_of_day = {row.day.date().isoformat(): row for row in stat_rows}
    days = [_day_stats(day.isoformat(), spend_of_day, stats_of_day) for day in period]
    top = sorted(models.items(), key=lambda item: item[1][2], reverse=True)[:TOP_MODELS]
    return StatsReport(
        days=days,
        top_models=[
            ModelUsage(model, int(total[0]), int(total[1]), round(total[2], 6))
            for model, total in top
        ],
    )


def _day_stats(
    day: str,
    spend_of_day: Mapping[str, Sequence[SpendRow]],
    stats_of_day: Mapping[str, StatRow],
) -> DayStats:
    rows = spend_of_day.get(day, ())
    stat = stats_of_day.get(day)
    return DayStats(
        day=day,
        requests=sum(row.requests for row in rows),
        tokens=sum(row.tokens for row in rows),
        spend=round(sum(row.spend for row in rows), 6),
        failures=_counts(stat.failures if stat else None),
        switches=_counts(stat.switches if stat else None),
        state_seconds=_counts(stat.state_seconds if stat else None),
    )


def _counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    counts = cast(Mapping[str, object], raw)
    return {
        key: round(value)
        for key, value in counts.items()
        if isinstance(value, int | float)
    }
