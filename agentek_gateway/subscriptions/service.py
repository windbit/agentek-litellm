from collections.abc import Callable
from dataclasses import dataclass

from .clock import Clock
from .config import GatewayConfig
from .events import Event, Expired
from .machine import Note, transition
from .model import StateRecord, Subscription, initial_record
from .ports import StateStore

MAX_CAS_ATTEMPTS = 8


class StateConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Applied:
    record: StateRecord
    changed: bool
    notes: tuple[Note, ...]


class StateService:
    def __init__(
        self,
        store: StateStore,
        clock: Clock,
        config: GatewayConfig,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._config = config
        self._on_changed = on_changed

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
