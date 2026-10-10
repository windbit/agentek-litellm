import asyncio
from collections.abc import Callable
from typing import Protocol

from redis.asyncio import Redis

from litellm._logging import verbose_proxy_logger

RESUBSCRIBE_DELAY_S = 1.0
LISTEN_POLL_S = 1.0


class Notifier(Protocol):
    async def publish(self) -> None: ...


class NullNotifier:
    async def publish(self) -> None:
        return None


class RedisNotifier:
    """Tells every replica that shared state changed so they refresh their snapshot at once."""

    def __init__(self, redis: Redis, channel: str) -> None:
        self._redis = redis
        self._channel = channel

    async def publish(self) -> None:
        try:
            await self._redis.publish(self._channel, "changed")
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception(
                "agentek_gateway change notification was not published"
            )


class RedisListener:
    """Calls on_change for each notification; a lost one is covered by the snapshot interval."""

    def __init__(
        self, redis: Redis, channel: str, on_change: Callable[[], None]
    ) -> None:
        self._redis = redis
        self._channel = channel
        self._on_change = on_change

    async def run(self) -> None:
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                verbose_proxy_logger.exception("agentek_gateway change listener failed")
            await asyncio.sleep(RESUBSCRIBE_DELAY_S)

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(self._channel)
            while True:
                # a read with its own timeout is not bound by the client's socket_timeout
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=LISTEN_POLL_S
                )
                if message is not None and message.get("type") == "message":
                    self._on_change()
        finally:
            await pubsub.aclose()
