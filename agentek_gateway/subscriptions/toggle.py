from .events import OperatorDisabled, OperatorEnabled
from .model import Subscription
from .ports import StateStore, SubscriptionRepo
from .service import StateService


class SubscriptionToggle:
    """Operator switch: the database row is written first, then the Redis flag and the notification that carry it to every replica."""

    def __init__(
        self, store: StateStore, repo: SubscriptionRepo, states: StateService
    ) -> None:
        self._store = store
        self._repo = repo
        self._states = states

    async def set_enabled(self, subscription: Subscription, enabled: bool) -> None:
        """Enabling returns the subscription through a liveness probe, never straight to ACTIVE."""
        await self._repo.set_enabled(subscription.id, enabled)
        await self._store.write_enabled_flag(subscription.id, enabled)
        event = OperatorEnabled() if enabled else OperatorDisabled()
        await self._states.apply(subscription, event)
