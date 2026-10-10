from dataclasses import replace
from typing import Protocol

from redis.asyncio import Redis

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .policy import Policy
from .ports import PolicyRepo

POLICY_MAX_AGE_S = 30.0


class PolicyVersions(Protocol):
    async def current(self) -> int: ...

    async def bump(self) -> None: ...


class RedisPolicyVersions:
    """Counter every replica compares to know whether its copy of the policy is current."""

    def __init__(self, redis: Redis, key: str) -> None:
        self._redis = redis
        self._key = key

    async def current(self) -> int:
        return int(await self._redis.get(self._key) or 0)

    async def bump(self) -> None:
        await self._redis.incr(self._key)


class CachedPolicyRepo:
    """Reads the database only when the shared version moved.

    The version is read before the data, so a change committed in between is picked up by the next check.
    A Redis that lost the counter or is down costs a database read, never a stale policy for longer than max_age_s.
    """

    def __init__(
        self,
        inner: PolicyRepo,
        versions: PolicyVersions,
        clock: Clock,
        max_age_s: float = POLICY_MAX_AGE_S,
    ) -> None:
        self._inner = inner
        self._versions = versions
        self._clock = clock
        self._max_age_s = max_age_s
        self._cached: tuple[Policy, float] | None = None

    async def load_policy(self) -> Policy:
        version = await self._version()
        cached = self._cached
        now = self._clock.now()
        if (
            cached is not None
            and version is not None
            and cached[0].version == version
            and now - cached[1] < self._max_age_s
        ):
            return cached[0]
        loaded = await self._inner.load_policy()
        policy = replace(loaded, version=version if version is not None else -1)
        self._cached = (policy, now)
        return policy

    async def _version(self) -> int | None:
        try:
            return await self._versions.current()
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway policy version unreadable")
            return None
