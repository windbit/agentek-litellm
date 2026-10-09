import asyncio

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .leader import LeaderLease
from .probes import ProbeLoop
from .refresher import TokenRefresher

DUTY_TICK_S = 5.0
REFRESH_CYCLE_S = 60.0


class LeaderDuties:
    """Runs probes and token upkeep on the replica that holds the lease."""

    def __init__(
        self,
        lease: LeaderLease,
        probes: ProbeLoop,
        refresher: TokenRefresher,
        clock: Clock,
    ) -> None:
        self._lease = lease
        self._probes = probes
        self._refresher = refresher
        self._clock = clock
        self._last_refresh_at = float("-inf")

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(DUTY_TICK_S)

    async def tick(self) -> bool:
        """One pass; False when this replica is not the leader."""
        try:
            if not await self._lease.hold():
                return False
            await self._probes.tick()
            now = self._clock.now()
            if now - self._last_refresh_at >= REFRESH_CYCLE_S:
                self._last_refresh_at = now
                await self._refresher.tick()
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway leader duties failed")
        return True
