import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Protocol

from prisma.errors import PrismaError
from redis.exceptions import RedisError

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .config import GatewayConfig
from .events import Event, Expired
from .machine import Note, Transition, transition
from .model import StateRecord, Subscription, SubscriptionId, initial_record
from .ports import StateStore

MAX_CAS_ATTEMPTS = 8
WRITE_RETRY_FIRST_S = 0.2
WRITE_RETRY_CAP_S = 5.0
WRITE_RETRY_BUDGET_S = 600.0
MAX_DEFERRED_EVENTS = 16
TRANSIENT_STORE_ERRORS = (OSError, TimeoutError, RedisError, PrismaError)


class StateConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Applied:
    record: StateRecord
    changed: bool
    notes: tuple[Note, ...]


class StateView(Protocol):
    """This process's picture of the states: decisions are made on it, and a state not yet written to Redis waits in it."""

    def local_record(self, subscription_id: SubscriptionId) -> StateRecord | None: ...

    def hold(self, subscription_id: SubscriptionId, record: StateRecord) -> None: ...

    def settle(self, subscription_id: SubscriptionId, record: StateRecord) -> None: ...

    def is_current(self) -> bool: ...


Spawn = Callable[[Coroutine[object, object, object]], None]
Sleep = Callable[[float], Coroutine[object, object, object]]


class StateService:
    def __init__(
        self,
        store: StateStore,
        clock: Clock,
        config: GatewayConfig,
        on_changed: Callable[[], None] | None = None,
        *,
        view: StateView | None = None,
        spawn: Spawn | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._store = store
        self._clock = clock
        self._config = config
        self._on_changed = on_changed
        self._view = view
        self._spawn = spawn
        self._sleep = sleep
        self._deferred: dict[SubscriptionId, list[Event]] = {}

    async def apply(self, subscription: Subscription, event: Event) -> Applied:
        tuning = self._config.tuning_for(subscription.provider)
        for _ in range(MAX_CAS_ATTEMPTS):
            now = self._clock.now()
            stored = await self._store.read_state(subscription.id)
            base = stored or initial_record(now)
            current = transition(base, Expired(), now, tuning).record
            result = transition(current, event, now, tuning)
            if not result.changed and current is base:
                return Applied(base, changed=False, notes=result.notes)
            if await self._store.compare_and_set_state(
                subscription.id, stored.version if stored else None, result.record
            ):
                if self._on_changed:
                    self._on_changed()
                return Applied(result.record, changed=True, notes=result.notes)
        raise StateConflictError(
            f"state of {subscription.id} kept changing during {MAX_CAS_ATTEMPTS} attempts"
        )

    async def record(self, subscription: Subscription, event: Event) -> Applied:
        """Outcome of a request: this process acts on it at once; Redis gets it with retries, even when unreachable now."""
        local = self.hold(subscription, event)
        try:
            applied = await self.apply(subscription, event)
        except StateConflictError:
            raise
        except TRANSIENT_STORE_ERRORS as error:
            if local is None or self._spawn is None:
                raise
            verbose_proxy_logger.warning(
                "agentek_gateway state of %s is not written yet, retrying",
                subscription.id,
                exc_info=error,
            )
            self._write_later(subscription, event)
            return Applied(local.record, local.changed, local.notes)
        self._settle(subscription.id, applied.record)
        return applied

    async def observe(self, subscription: Subscription, event: Event) -> None:
        """For events that matter only when they change the state: judged on this process's picture before Redis is asked."""
        local = self._local_transition(subscription, event)
        unchanged = local is not None and not local.changed
        if unchanged and self._view is not None and self._view.is_current():
            return
        await self.apply(subscription, event)

    def hold(self, subscription: Subscription, event: Event) -> Transition | None:
        """Applies the event to this process's picture alone; None when there is no picture to apply it to."""
        local = self._local_transition(subscription, event)
        if local is not None and local.changed and self._view is not None:
            self._view.hold(subscription.id, local.record)
        return local

    def _local_transition(
        self, subscription: Subscription, event: Event
    ) -> Transition | None:
        """The event applied to the local record; changed also when only the expiry of the old state moved it."""
        if self._view is None:
            return None
        now = self._clock.now()
        tuning = self._config.tuning_for(subscription.provider)
        base = self._view.local_record(subscription.id)
        if base is None:
            return None
        expired = transition(base, Expired(), now, tuning).record
        result = transition(expired, event, now, tuning)
        if result.changed or expired is base:
            return result
        return Transition(result.record, True, result.notes)

    def _settle(self, subscription_id: SubscriptionId, record: StateRecord) -> None:
        if self._view is not None:
            self._view.settle(subscription_id, record)

    def _write_later(self, subscription: Subscription, event: Event) -> None:
        """Queued events are applied one after another, so the state machine decides which of them wins."""
        queue = self._deferred.get(subscription.id)
        if queue is not None:
            queue.append(event)
            del queue[:-MAX_DEFERRED_EVENTS]
            return
        self._deferred[subscription.id] = [event]
        if self._spawn is not None:
            self._spawn(self._write_until_done(subscription))

    async def _write_until_done(self, subscription: Subscription) -> None:
        delay_s = WRITE_RETRY_FIRST_S
        started = self._clock.now()
        queue = self._deferred[subscription.id]
        try:
            while queue and self._clock.now() - started < WRITE_RETRY_BUDGET_S:
                await self._sleep(delay_s)
                delay_s = min(delay_s * 2, WRITE_RETRY_CAP_S)
                await self._write_queued(subscription, queue)
            if queue:
                verbose_proxy_logger.error(
                    "agentek_gateway gave up writing the state of %s", subscription.id
                )
        finally:
            del self._deferred[subscription.id]

    async def _write_queued(
        self, subscription: Subscription, queue: list[Event]
    ) -> None:
        while queue:
            event = queue[0]
            try:
                applied = await self.apply(subscription, event)
            except StateConflictError:
                return
            except TRANSIENT_STORE_ERRORS as error:
                verbose_proxy_logger.warning(
                    "agentek_gateway state of %s is still not written (%s)",
                    subscription.id,
                    type(error).__name__,
                )
                return
            self._settle(subscription.id, applied.record)
            queue.remove(event)
