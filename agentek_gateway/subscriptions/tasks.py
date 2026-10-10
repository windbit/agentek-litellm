import asyncio
from collections.abc import Coroutine

from litellm._logging import verbose_proxy_logger


class BackgroundTasks:
    """Fire-and-forget work that keeps a reference until done and logs what it raised."""

    def __init__(self) -> None:
        self._running: set[asyncio.Task[object]] = set()

    def spawn(self, work: Coroutine[object, object, object]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._running.add(task)
        task.add_done_callback(self._finished)

    @property
    def running(self) -> tuple[asyncio.Task[object], ...]:
        return tuple(self._running)

    async def cancel_all(self) -> None:
        running = tuple(self._running)
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    async def drain_within(self, timeout_s: float) -> None:
        """Waits for the running work up to the timeout; whatever is left is cancelled."""
        try:
            await asyncio.wait_for(self.drain(), timeout_s)
        except TimeoutError:
            await self.cancel_all()

    async def drain(self) -> None:
        while self._running:
            await asyncio.gather(*tuple(self._running), return_exceptions=True)

    def _finished(self, task: asyncio.Task[object]) -> None:
        self._running.discard(task)
        if not task.cancelled() and task.exception() is not None:
            verbose_proxy_logger.error(
                "agentek_gateway background task failed", exc_info=task.exception()
            )
