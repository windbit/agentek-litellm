from collections.abc import Mapping, Sequence

from redis.asyncio import Redis

from .clock import Clock
from .model import SubscriptionId

KEY_GRACE_S = 60


class RedisSlotStore:
    """Slots as a sorted set per subscription, score = expiry: release is idempotent and abandoned slots age out."""

    def __init__(self, redis: Redis, clock: Clock, prefix: str) -> None:
        self._redis = redis
        self._clock = clock
        self._prefix = prefix

    async def reserve(
        self,
        subscription_id: SubscriptionId,
        token: str,
        limit: int | None,
        ttl_s: float,
    ) -> bool:
        key = self._key(subscription_id)
        now = self._clock.now()
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, "-inf", now)
            pipe.zadd(key, {token: now + ttl_s})
            pipe.zcard(key)
            pipe.expire(key, int(ttl_s + KEY_GRACE_S))
            _, _, held, _ = await pipe.execute()
        if limit is not None and held > limit:
            await self._redis.zrem(key, token)
            return False
        return True

    async def release(self, subscription_id: SubscriptionId, token: str) -> bool:
        return await self._redis.zrem(self._key(subscription_id), token) > 0

    async def extend(
        self, subscription_id: SubscriptionId, token: str, ttl_s: float
    ) -> bool:
        key = self._key(subscription_id)
        extended = await self._redis.zadd(
            key, {token: self._clock.now() + ttl_s}, xx=True, ch=True
        )
        await self._redis.expire(key, int(ttl_s + KEY_GRACE_S))
        return extended > 0

    async def in_flight(
        self, subscription_ids: Sequence[SubscriptionId]
    ) -> Mapping[SubscriptionId, int]:
        if not subscription_ids:
            return {}
        now = self._clock.now()
        async with self._redis.pipeline(transaction=False) as pipe:
            for sub_id in subscription_ids:
                pipe.zcount(self._key(sub_id), now, "+inf")
            counts = await pipe.execute()
        return dict(zip(subscription_ids, counts, strict=True))

    def _key(self, subscription_id: SubscriptionId) -> str:
        return f"{self._prefix}slots:{subscription_id}"
