import uuid

from redis.asyncio import Redis

LEASE_TTL_S = 30


class LeaderLease:
    """One replica at a time runs the background duties; a dead leader loses the lease within the TTL."""

    def __init__(self, redis: Redis, key: str, ttl_s: int = LEASE_TTL_S) -> None:
        self._redis = redis
        self._key = key
        self._ttl_s = ttl_s
        self._holder = uuid.uuid4().hex

    async def hold(self) -> bool:
        """Takes the lease when free and renews it when already held; False when another replica leads."""
        if await self._redis.set(self._key, self._holder, nx=True, ex=self._ttl_s):
            return True
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.watch(self._key)
            if await pipe.get(self._key) != self._holder:
                return False
            pipe.multi()
            pipe.expire(self._key, self._ttl_s)
            await pipe.execute()
        return True

    async def release(self) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.watch(self._key)
            if await pipe.get(self._key) == self._holder:
                pipe.multi()
                pipe.delete(self._key)
                await pipe.execute()
