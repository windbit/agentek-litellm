import json
import time
from collections.abc import Mapping, Sequence

from redis.asyncio import Redis
from redis.exceptions import WatchError

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .expiring import ExpiringMap
from .model import (
    DURABLE_STATES,
    EgressInfo,
    Limits,
    Route,
    StateRecord,
    Subscription,
    SubscriptionId,
    UsageRecord,
    UsageSource,
    Window,
)
from .notify import Notifier, NullNotifier
from .ports import StateDb
from .redis_keys import Keys, route_from_member, route_member
from .state_codec import decode_record, encode_record

ENABLED = "1"
DISABLED = "0"
EXPIRY_GRACE_S = 60
ACTIVE_RECORD_TTL_S = 24 * 3600
ABSENT_IN_DB_TTL_S = 30.0
MAX_VERSION = 2**31 - 1
SERIES_KEY_FACTOR = 2
UNSUPPORTED_SEPARATOR = "\x1f"


def needs_durable_row(record: StateRecord) -> bool:
    """The states the spec keeps in the database, and the overload streak whatever the state."""
    return record.overload_streak > 0 or record.state in DURABLE_STATES


class RedisStateStore:
    """StateStore on Redis; durable states are mirrored into the database and restored when Redis forgets them.

    The Redis client must decode responses to str.
    """

    def __init__(
        self,
        redis: Redis,
        db: StateDb,
        clock: Clock,
        keys: Keys,
        notifier: Notifier | None = None,
    ) -> None:
        self._redis = redis
        self._db = db
        self._clock = clock
        self._keys = keys
        self._notifier = notifier or NullNotifier()
        self._absent_in_db: ExpiringMap[SubscriptionId, bool] = ExpiringMap(
            clock, ABSENT_IN_DB_TTL_S
        )

    async def load_durable(self) -> None:
        """Puts back whatever Redis lost; call before subscriptions are served."""
        for subscription_id, record in (await self._db.read_all_states()).items():
            await self._restore(subscription_id, record)

    async def reconcile(self, subscription_ids: Sequence[SubscriptionId]) -> None:
        """Brings the database and Redis back in line after a write that reached only one of them."""
        rows = await self._db.read_all_states()
        in_redis = await self._read_redis(subscription_ids)
        for subscription_id in {*subscription_ids, *rows}:
            record, row = in_redis.get(subscription_id), rows.get(subscription_id)
            if record is None and row is not None:
                await self._restore(subscription_id, row)
            elif record is not None and (row is None or row.version < record.version):
                await self._mirror(subscription_id, record)

    async def reconcile_enabled_flags(
        self, subscriptions: Sequence[Subscription]
    ) -> None:
        """The database decides: a flag that disagrees with the row (a write that never reached Redis) is rewritten."""
        flags = await self.read_enabled_flags()
        for subscription in subscriptions:
            flag = flags.get(subscription.id)
            if flag is not None and flag != subscription.enabled:
                await self.write_enabled_flag(subscription.id, subscription.enabled)

    async def read_state(self, subscription_id: SubscriptionId) -> StateRecord | None:
        return (await self.read_states([subscription_id])).get(subscription_id)

    async def read_states(
        self, subscription_ids: Sequence[SubscriptionId]
    ) -> Mapping[SubscriptionId, StateRecord]:
        found = dict(await self._read_redis(subscription_ids))
        missing = [
            sub_id
            for sub_id in subscription_ids
            if sub_id not in found and sub_id not in self._absent_in_db
        ]
        if not missing:
            return found
        rows = await self._db.read_states(missing)
        for subscription_id in missing:
            row = rows.get(subscription_id)
            if row is None:
                self._absent_in_db.put(subscription_id, True)
                continue
            await self._restore(subscription_id, row)
            found[subscription_id] = row
        return found

    async def compare_and_set_state(
        self,
        subscription_id: SubscriptionId,
        expected_version: int | None,
        record: StateRecord,
    ) -> bool:
        key = self._keys.state(subscription_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                current = (
                    decode_record(raw)
                    if raw is not None
                    else await self._db.read_state(subscription_id)
                )
                if (current.version if current else None) != expected_version:
                    return False
                pipe.multi()
                pipe.set(key, encode_record(record), ex=self._ttl_for(record))
                await pipe.execute()
            except WatchError:
                return False
        self._absent_in_db.discard(subscription_id)
        await self._after_write(subscription_id, record)
        return True

    async def record_unclassified(
        self, subscription_id: SubscriptionId, window_s: float
    ) -> int:
        now = self._clock.now()
        member = f"{now}:{time.monotonic_ns()}"
        errors = self._keys.errors(subscription_id)
        series = self._keys.series(subscription_id)
        ttl = int(window_s * SERIES_KEY_FACTOR)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(errors, "-inf", now - window_s)
            pipe.zadd(errors, {member: now})
            pipe.expire(errors, ttl)
            pipe.zremrangebyscore(series, "-inf", now - window_s)
            pipe.zadd(series, {member: now})
            pipe.zcard(series)
            pipe.expire(series, ttl)
            results = await pipe.execute()
        return int(results[-2])

    async def unclassified_counts(
        self, subscription_ids: Sequence[SubscriptionId], window_s: float
    ) -> Mapping[SubscriptionId, int]:
        if not subscription_ids:
            return {}
        now = self._clock.now()
        async with self._redis.pipeline(transaction=False) as pipe:
            for sub_id in subscription_ids:
                pipe.zcount(self._keys.errors(sub_id), now - window_s, "+inf")
            counts = await pipe.execute()
        return dict(zip(subscription_ids, counts, strict=True))

    async def reset_series(self, subscription_id: SubscriptionId) -> None:
        await self._redis.delete(self._keys.series(subscription_id))

    async def mark_route_degraded(self, route: Route, window_s: float) -> None:
        await self._redis.zadd(
            self._keys.degraded, {route_member(route): self._clock.now() + window_s}
        )

    async def degraded_routes(self) -> frozenset[Route]:
        now = self._clock.now()
        members = await self._redis.zrangebyscore(self._keys.degraded, now, "+inf")
        return frozenset(route_from_member(member) for member in members)

    async def read_sticky(self, key: str) -> SubscriptionId | None:
        return await self._redis.get(self._keys.sticky(key))

    async def write_sticky(
        self, key: str, subscription_id: SubscriptionId, ttl_s: float
    ) -> None:
        await self._redis.set(self._keys.sticky(key), subscription_id, ex=int(ttl_s))

    async def mark_model_unsupported(
        self, subscription_id: SubscriptionId, model: str, ttl_s: float
    ) -> None:
        member = f"{subscription_id}{UNSUPPORTED_SEPARATOR}{model}"
        await self._redis.zadd(
            self._keys.unsupported, {member: self._clock.now() + ttl_s}
        )

    async def unsupported_pairs(self) -> frozenset[tuple[SubscriptionId, str]]:
        now = self._clock.now()
        members = await self._redis.zrangebyscore(self._keys.unsupported, now, "+inf")
        return frozenset(
            (sub_id, model)
            for sub_id, _, model in (
                member.partition(UNSUPPORTED_SEPARATOR) for member in members
            )
        )

    async def clear_unsupported(self, subscription_id: SubscriptionId) -> None:
        members = await self._redis.zrange(self._keys.unsupported, 0, -1)
        stale = [
            member
            for member in members
            if member.startswith(f"{subscription_id}{UNSUPPORTED_SEPARATOR}")
        ]
        if stale:
            await self._redis.zrem(self._keys.unsupported, *stale)

    async def write_usage(
        self, subscription_id: SubscriptionId, usage: UsageRecord
    ) -> None:
        await self._redis.hset(self._keys.usage, subscription_id, encode_usage(usage))

    async def read_all_usage(self) -> Mapping[SubscriptionId, UsageRecord]:
        raw = await self._redis.hgetall(self._keys.usage)
        return {sub_id: decode_usage(payload) for sub_id, payload in raw.items()}

    async def write_enabled_flag(
        self, subscription_id: SubscriptionId, enabled: bool
    ) -> None:
        await self._redis.hset(
            self._keys.enabled, subscription_id, ENABLED if enabled else DISABLED
        )
        await self._notifier.publish()

    async def read_enabled_flags(self) -> Mapping[SubscriptionId, bool]:
        raw = await self._redis.hgetall(self._keys.enabled)
        return {sub_id: value == ENABLED for sub_id, value in raw.items()}

    async def claim_limits_refresh(
        self, subscription_id: SubscriptionId, window_s: float
    ) -> bool:
        taken = await self._redis.set(
            self._keys.limits_refresh(subscription_id), "1", nx=True, ex=int(window_s)
        )
        return bool(taken)

    async def forget_subscription(self, subscription_id: SubscriptionId) -> None:
        """Drops everything Redis holds about a deleted subscription; the database row goes with the subscription."""
        await self._redis.delete(
            self._keys.state(subscription_id),
            self._keys.errors(subscription_id),
            self._keys.series(subscription_id),
            self._keys.limits_refresh(subscription_id),
        )
        await self._redis.hdel(self._keys.usage, subscription_id)
        await self._redis.hdel(self._keys.enabled, subscription_id)
        await self.clear_unsupported(subscription_id)
        await self._db.delete_state(subscription_id, MAX_VERSION)
        self._absent_in_db.discard(subscription_id)
        await self._notifier.publish()

    async def mark_refreshed(self, credential_name: str, window_s: float) -> None:
        await self._redis.set(
            self._keys.refreshed(credential_name), "1", ex=int(window_s)
        )

    async def recently_refreshed(self, credential_name: str) -> bool:
        return bool(await self._redis.exists(self._keys.refreshed(credential_name)))

    async def mark_probed(self, provider: str, at: float) -> None:
        await self._redis.hset(self._keys.probed, provider, str(at))

    async def read_probe_times(self) -> Mapping[str, float]:
        raw = await self._redis.hgetall(self._keys.probed)
        return {provider: float(value) for provider, value in raw.items()}

    async def write_egress(self, route: Route, info: EgressInfo) -> None:
        payload = json.dumps(
            {"ip": info.ip, "colo": info.colo, "observed_at": info.observed_at}
        )
        await self._redis.hset(self._keys.egress, route_member(route), payload)

    async def read_egress(self) -> Mapping[Route, EgressInfo]:
        raw = await self._redis.hgetall(self._keys.egress)
        return {
            route_from_member(member): EgressInfo(**json.loads(payload))
            for member, payload in raw.items()
        }

    async def _read_redis(
        self, subscription_ids: Sequence[SubscriptionId]
    ) -> dict[SubscriptionId, StateRecord]:
        if not subscription_ids:
            return {}
        values = await self._redis.mget(
            [self._keys.state(sub_id) for sub_id in subscription_ids]
        )
        return {
            sub_id: decode_record(value)
            for sub_id, value in zip(subscription_ids, values, strict=True)
            if value is not None
        }

    async def _restore(
        self, subscription_id: SubscriptionId, record: StateRecord
    ) -> None:
        await self._redis.set(
            self._keys.state(subscription_id),
            encode_record(record),
            ex=self._ttl_for(record),
            nx=True,
        )

    async def _after_write(
        self, subscription_id: SubscriptionId, record: StateRecord
    ) -> None:
        try:
            await self._mirror(subscription_id, record)
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception(
                "agentek_gateway state of %s was not written to the database",
                subscription_id,
            )
        await self._notifier.publish()

    async def _mirror(
        self, subscription_id: SubscriptionId, record: StateRecord
    ) -> None:
        """The database ignores a write older than the row it holds, so a late writer cannot undo a newer state."""
        if needs_durable_row(record):
            await self._db.write_state(subscription_id, record)
        else:
            await self._db.delete_state(subscription_id, record.version)

    def _ttl_for(self, record: StateRecord) -> int | None:
        if record.until is not None:
            return max(1, int(record.until - self._clock.now()) + EXPIRY_GRACE_S)
        if record.state in DURABLE_STATES:
            return None
        return ACTIVE_RECORD_TTL_S


def encode_usage(usage: UsageRecord) -> str:
    def window(item: Window | None) -> dict[str, float] | None:
        return (
            None
            if item is None
            else {"used": item.used_percent, "reset_at": item.reset_at}
        )

    return json.dumps(
        {
            "five_hour": window(usage.limits.five_hour),
            "weekly": window(usage.limits.weekly),
            "observed_at": usage.observed_at,
            "source": usage.source.value,
        }
    )


def decode_usage(raw: str) -> UsageRecord:
    fields = json.loads(raw)

    def window(item: Mapping[str, float] | None) -> Window | None:
        return None if item is None else Window(item["used"], item["reset_at"])

    return UsageRecord(
        Limits(five_hour=window(fields["five_hour"]), weekly=window(fields["weekly"])),
        fields["observed_at"],
        UsageSource(fields.get("source", UsageSource.RESPONSE_HEADERS)),
    )
