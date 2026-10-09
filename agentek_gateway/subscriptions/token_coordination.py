import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import WatchError

from .credentials import auth_from_mapping, auth_to_mapping
from .providers.chatgpt import ChatgptAuth
from .redis_keys import Keys

LOCK_TTL_S = 60
RECENT_REFRESH_WINDOW_S = 60.0
CREDENTIALS_RELOAD_INTERVAL_S = 30
UNSAVED_LATEST_TTL_S = 24 * 3600
SAVED_LATEST_TTL_S = 2 * CREDENTIALS_RELOAD_INTERVAL_S
LOCK_POLL_S = 0.05
LOCK_WAIT_S = 1.0


@dataclass(frozen=True, slots=True)
class LatestAuth:
    """Newest token pair a refresh produced; persisted=False until the database has it, then kept briefly for replicas still holding the old pair."""

    auth: ChatgptAuth
    persisted: bool = False


def encode_latest(latest: LatestAuth) -> str:
    return json.dumps(
        {"auth": auth_to_mapping(latest.auth), "persisted": latest.persisted}
    )


def decode_latest(raw: str | None) -> LatestAuth | None:
    if raw is None:
        return None
    fields = json.loads(raw)
    auth = auth_from_mapping(fields.get("auth"))
    return LatestAuth(auth, bool(fields.get("persisted"))) if auth else None


def latest_ttl_s(latest: LatestAuth) -> int:
    return SAVED_LATEST_TTL_S if latest.persisted else UNSAVED_LATEST_TTL_S


class TokenCoordinator:
    """Cross-replica protocol for refreshing a token: one lock per credential and the newest pair before it is persisted."""

    def __init__(self, redis: Redis, keys: Keys) -> None:
        self._redis = redis
        self._keys = keys

    async def acquire(self, credential_name: str) -> str | None:
        token = uuid.uuid4().hex
        taken = await self._redis.set(
            self._keys.refresh_lock(credential_name), token, nx=True, ex=LOCK_TTL_S
        )
        return token if taken else None

    async def release(self, credential_name: str, token: str) -> None:
        key = self._keys.refresh_lock(credential_name)
        async with self._redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                if await pipe.get(key) == token:
                    pipe.multi()
                    pipe.delete(key)
                    await pipe.execute()
            except WatchError:
                return

    async def clear_latest(self, credential_name: str) -> None:
        await self._redis.delete(self._keys.latest_auth(credential_name))

    async def save_latest(self, credential_name: str, latest: LatestAuth) -> None:
        await self._redis.set(
            self._keys.latest_auth(credential_name),
            encode_latest(latest),
            ex=latest_ttl_s(latest),
        )

    async def read_latest(self, credential_name: str) -> LatestAuth | None:
        return decode_latest(
            await self._redis.get(self._keys.latest_auth(credential_name))
        )


class SyncTokenCoordinator:
    """The same protocol for the authenticator, which refreshes inside a synchronous request path."""

    def __init__(
        self,
        redis: SyncRedis,
        keys: Keys,
        sleep: Callable[[float], None] = time.sleep,
        lock_wait_s: float = LOCK_WAIT_S,
    ) -> None:
        self._redis = redis
        self._keys = keys
        self._sleep = sleep
        self._lock_wait_s = lock_wait_s

    def acquire(self, credential_name: str) -> str | None:
        deadline = time.monotonic() + self._lock_wait_s
        token = uuid.uuid4().hex
        key = self._keys.refresh_lock(credential_name)
        while time.monotonic() < deadline:
            if self._redis.set(key, token, nx=True, ex=LOCK_TTL_S):
                return token
            self._sleep(LOCK_POLL_S)
        return None

    def release(self, credential_name: str, token: str) -> None:
        key = self._keys.refresh_lock(credential_name)
        with self._redis.pipeline(transaction=True) as pipe:
            try:
                pipe.watch(key)
                if pipe.get(key) == token:
                    pipe.multi()
                    pipe.delete(key)
                    pipe.execute()
            except WatchError:
                return

    def save_latest(self, credential_name: str, latest: LatestAuth) -> None:
        self._redis.set(
            self._keys.latest_auth(credential_name),
            encode_latest(latest),
            ex=latest_ttl_s(latest),
        )

    def read_latest(self, credential_name: str) -> LatestAuth | None:
        return decode_latest(self._redis.get(self._keys.latest_auth(credential_name)))

    def mark_refreshed(self, credential_name: str, window_s: float) -> None:
        self._redis.set(self._keys.refreshed(credential_name), "1", ex=int(window_s))
