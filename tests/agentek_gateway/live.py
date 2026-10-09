"""Real Postgres and Redis for the tests that fakeredis and in-memory fakes cannot stand in for.

AGENTEK_TEST_DATABASE_URL must point at a database that already has the schema (prisma db push) and
AGENTEK_TEST_REDIS_URL at a Redis that may be flushed; without them these tests are skipped.
"""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from redis.asyncio import Redis

DATABASE_URL = os.environ.get("AGENTEK_TEST_DATABASE_URL")
REDIS_URL = os.environ.get("AGENTEK_TEST_REDIS_URL")

needs_postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="AGENTEK_TEST_DATABASE_URL is not set"
)
needs_redis = pytest.mark.skipif(
    not REDIS_URL, reason="AGENTEK_TEST_REDIS_URL is not set"
)

AGENTEK_TABLES = (
    "litellm_agenteksubscriptionstate",
    "litellm_agenteksubscriptionpolicy",
    "litellm_agenteksubscription",
)


@asynccontextmanager
async def live_db():  # type: ignore[no-untyped-def]
    from prisma import Prisma

    client = Prisma(datasource={"url": DATABASE_URL})
    await client.connect()
    try:
        for table in AGENTEK_TABLES:
            await getattr(client, table).delete_many()
        await client.litellm_credentialstable.delete_many()
        yield client
    finally:
        await client.disconnect()


@asynccontextmanager
async def live_redis() -> AsyncIterator[Redis]:
    redis = Redis.from_url(REDIS_URL, decode_responses=True)  # type: ignore[arg-type]
    await redis.flushdb()
    try:
        yield redis
    finally:
        await redis.aclose()


class InterferingPipeline:
    """A pipeline that lets another client change the watched key right after the first read."""

    def __init__(
        self, pipeline, interfere: Callable[[], Awaitable[None]]  # type: ignore[no-untyped-def]
    ) -> None:
        self._pipeline = pipeline
        self._interfere = interfere
        self._done = False

    async def __aenter__(self) -> "InterferingPipeline":
        self._real = await self._pipeline.__aenter__()
        return self

    async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
        return await self._pipeline.__aexit__(*exc)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        attribute = getattr(self._real, name)
        if name != "get":
            return attribute

        async def read_then_interfere(*args, **kwargs):  # type: ignore[no-untyped-def]
            value = await attribute(*args, **kwargs)
            if not self._done:
                self._done = True
                await self._interfere()
            return value

        return read_then_interfere


class InterferingRedis:
    def __init__(self, redis: Redis, interfere: Callable[[], Awaitable[None]]) -> None:
        self._redis = redis
        self._interfere = interfere

    def pipeline(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return InterferingPipeline(
            self._redis.pipeline(*args, **kwargs), self._interfere
        )

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._redis, name)


class SyncInterferingPipeline:
    def __init__(self, pipeline, interfere: Callable[[], None]) -> None:  # type: ignore[no-untyped-def]
        self._pipeline = pipeline
        self._interfere = interfere
        self._done = False

    def __enter__(self) -> "SyncInterferingPipeline":
        self._real = self._pipeline.__enter__()
        return self

    def __exit__(self, *exc):  # type: ignore[no-untyped-def]
        return self._pipeline.__exit__(*exc)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        attribute = getattr(self._real, name)
        if name != "get":
            return attribute

        def read_then_interfere(*args, **kwargs):  # type: ignore[no-untyped-def]
            value = attribute(*args, **kwargs)
            if not self._done:
                self._done = True
                self._interfere()
            return value

        return read_then_interfere


class SyncInterferingRedis:
    def __init__(self, redis, interfere: Callable[[], None]) -> None:  # type: ignore[no-untyped-def]
        self._redis = redis
        self._interfere = interfere

    def pipeline(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return SyncInterferingPipeline(
            self._redis.pipeline(*args, **kwargs), self._interfere
        )

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._redis, name)
