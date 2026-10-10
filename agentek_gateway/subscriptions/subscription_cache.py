from collections.abc import Sequence

from .clock import Clock
from .model import Subscription, SubscriptionId
from .ports import SubscriptionRepo

DIRECTORY_TTL_S = 5.0


class CachedSubscriptionRepo:
    """Short-lived copy of the subscription list for callers that read it on every error or tick."""

    def __init__(
        self, repo: SubscriptionRepo, clock: Clock, ttl_s: float = DIRECTORY_TTL_S
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._ttl_s = ttl_s
        self._cached: tuple[float, Sequence[Subscription]] | None = None

    async def list_subscriptions(self) -> Sequence[Subscription]:
        now = self._clock.now()
        if self._cached is None or now - self._cached[0] >= self._ttl_s:
            self._cached = (now, await self._repo.list_subscriptions())
        return self._cached[1]

    async def set_enabled(self, subscription_id: SubscriptionId, enabled: bool) -> None:
        await self._repo.set_enabled(subscription_id, enabled)
        self.invalidate()

    def invalidate(self) -> None:
        self._cached = None
