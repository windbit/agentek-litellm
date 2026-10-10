from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol, cast

from .gateway import CREDENTIAL_TAG_PREFIX
from .model import Subscription, SubscriptionId

MAX_DAYS = 30
TOP_MODELS = 5


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


class SpendRow(Protocol):
    tag: str | None
    date: str
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    spend: float
    successful_requests: int


class StatRow(Protocol):
    subscription_id: str
    day: datetime
    failures: object
    switches: object
    state_seconds: object


class SpendTable(Protocol):
    async def find_many(self, *, where: Mapping[str, object]) -> Sequence[SpendRow]: ...


class StatTable(Protocol):
    async def find_many(self, *, where: Mapping[str, object]) -> Sequence[StatRow]: ...


class PrismaStatsReader:
    """Spend and tokens come from LiteLLM's per-tag daily rows (tag "Credential: <name>", written for the subscription
    that served the successful attempt); failures, switches and time in states from the plugin's daily rows.
    """

    def __init__(
        self, spend: Callable[[], SpendTable], stats: Callable[[], StatTable]
    ) -> None:
        self._spend = spend
        self._stats = stats

    async def report(
        self, subscriptions: Sequence[Subscription], days: int, today: date
    ) -> dict[SubscriptionId, StatsReport]:
        first = today - timedelta(days=days - 1)
        tags = {
            sub.id: CREDENTIAL_TAG_PREFIX + sub.credential_name for sub in subscriptions
        }
        spend_rows = await self._spend().find_many(
            where={
                "tag": {"in": list(tags.values())},
                "date": {"gte": first.isoformat()},
            }
        )
        stat_rows = await self._stats().find_many(
            where={"day": {"gte": datetime.combine(first, time.min)}}
        )
        spend_by_tag: dict[str, list[SpendRow]] = defaultdict(list)
        for spend_row in spend_rows:
            spend_by_tag[spend_row.tag or ""].append(spend_row)
        stats_by_id: dict[str, list[StatRow]] = defaultdict(list)
        for stat_row in stat_rows:
            stats_by_id[stat_row.subscription_id].append(stat_row)
        period = [first + timedelta(days=offset) for offset in range(days)]
        return {
            sub.id: build_report(
                period, spend_by_tag.get(tags[sub.id], ()), stats_by_id.get(sub.id, ())
            )
            for sub in subscriptions
        }


def build_report(
    period: Sequence[date], spend_rows: Sequence[SpendRow], stat_rows: Sequence[StatRow]
) -> StatsReport:
    spend_of_day: dict[str, list[SpendRow]] = defaultdict(list)
    models: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for row in spend_rows:
        spend_of_day[row.date].append(row)
        total = models[row.model or "unknown"]
        total[0] += row.successful_requests
        total[1] += row.prompt_tokens + row.completion_tokens
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
        requests=sum(row.successful_requests for row in rows),
        tokens=sum(row.prompt_tokens + row.completion_tokens for row in rows),
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
