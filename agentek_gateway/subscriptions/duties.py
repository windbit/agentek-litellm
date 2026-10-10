import asyncio
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .egress import EgressWatcher
from .leader import LeaderLease
from .model_copies import COPY_SYNC_INTERVAL_S
from .probes import ProbeLoop
from .refresher import TokenRefresher

DUTY_TICK_S = 5.0
REFRESH_CYCLE_S = 60.0
EGRESS_CYCLE_S = 10 * 60.0


class Upkeep(Protocol):
    async def tick(self) -> None: ...


class LeaderDuties:
    """Runs probes and token upkeep on the replica that holds the lease."""

    def __init__(
        self,
        lease: LeaderLease,
        probes: ProbeLoop,
        refresher: TokenRefresher,
        egress: EgressWatcher,
        clock: Clock,
        catalog: Upkeep | None = None,
    ) -> None:
        self._lease = lease
        self._probes = probes
        self._refresher = refresher
        self._egress = egress
        self._clock = clock
        self._catalog = catalog
        self._last_catalog_at = float("-inf")
        self._last_refresh_at = float("-inf")
        self._last_egress_at = float("-inf")

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(DUTY_TICK_S)

    async def tick(self) -> bool:
        """One pass; False when this replica is not the leader."""
        try:
            if not await self._lease.hold():
                return False
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway leader lease failed")
            return False
        try:
            await self._probes.tick()
            now = self._clock.now()
            if self._catalog and now - self._last_catalog_at >= COPY_SYNC_INTERVAL_S:
                self._last_catalog_at = now
                await self._catalog.tick()
            if now - self._last_refresh_at >= REFRESH_CYCLE_S:
                self._last_refresh_at = now
                await self._refresher.tick()
            if now - self._last_egress_at >= EGRESS_CYCLE_S:
                self._last_egress_at = now
                await self._egress.tick()
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway leader duties failed")
        return True
