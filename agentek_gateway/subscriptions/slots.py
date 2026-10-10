import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .expiring import ExpiringMap
from .model import Subscription, SubscriptionId
from .ports import SlotStore

DEFAULT_MAX_TRACKED_REQUESTS = 50_000
EXPIRED_SWEEP_INTERVAL_S = 1.0


@dataclass(frozen=True, slots=True)
class Reservation:
    token: str
    request_id: str
    deployment_id: str
    subscription_id: SubscriptionId
    model_group: str
    attempt: int
    alternatives: int


@dataclass(frozen=True, slots=True)
class ReserveRequest:
    request_id: str
    deployment_id: str
    subscription: Subscription
    model_group: str
    alternatives: int
    limit: int | None


class _RequestSlots:
    __slots__ = ("attempts", "by_deployment", "released")

    def __init__(self) -> None:
        self.by_deployment: dict[str, Reservation] = {}
        self.released: set[str] = set()
        self.attempts = 0


class InMemorySlotStore:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._held: dict[SubscriptionId, dict[str, float]] = {}

    async def reserve(
        self,
        subscription_id: SubscriptionId,
        token: str,
        limit: int | None,
        ttl_s: float,
    ) -> bool:
        now = self._clock.now()
        held = {
            held_token: expires_at
            for held_token, expires_at in self._held.get(subscription_id, {}).items()
            if expires_at > now
        }
        if limit is not None and len(held) >= limit:
            self._held[subscription_id] = held
            return False
        held[token] = now + ttl_s
        self._held[subscription_id] = held
        return True

    async def release(self, subscription_id: SubscriptionId, token: str) -> bool:
        return self._held.get(subscription_id, {}).pop(token, None) is not None

    async def extend(
        self, subscription_id: SubscriptionId, token: str, ttl_s: float
    ) -> bool:
        held = self._held.get(subscription_id, {})
        if token not in held:
            return False
        held[token] = self._clock.now() + ttl_s
        return True

    async def in_flight(
        self, subscription_ids: Sequence[SubscriptionId]
    ) -> Mapping[SubscriptionId, int]:
        now = self._clock.now()
        return {
            sub_id: sum(
                1
                for expires_at in self._held.get(sub_id, {}).values()
                if expires_at > now
            )
            for sub_id in subscription_ids
        }


class SlotLedger:
    """Per-request bookkeeping over a SlotStore: one reservation token per attempt, released exactly once."""

    def __init__(
        self,
        store: SlotStore,
        clock: Clock,
        ttl_s: float,
        max_requests: int = DEFAULT_MAX_TRACKED_REQUESTS,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ttl_s = ttl_s
        self._requests: ExpiringMap[str, _RequestSlots] = ExpiringMap(
            clock, ttl_s, max_requests
        )
        self._here: dict[SubscriptionId, int] = {}
        self._live: dict[str, tuple[SubscriptionId, float]] = {}
        self._swept_at = clock.now()

    async def reserve(self, request: ReserveRequest) -> Reservation | None:
        """Idempotent per (request, deployment); moving to a new deployment releases the request's earlier slots."""
        slots = self._requests.get(request.request_id) or self._open(request.request_id)
        existing = slots.by_deployment.get(request.deployment_id)
        if existing:
            return existing
        await self._release_live(slots)
        token = uuid.uuid4().hex
        self._track(token, request.subscription.id)
        try:
            reserved = await self._store.reserve(
                request.subscription.id, token, request.limit, self._ttl_s
            )
        except BaseException:
            self._untrack(token)
            raise
        if not reserved:
            self._untrack(token)
            return None
        slots.attempts += 1
        reservation = Reservation(
            token=token,
            request_id=request.request_id,
            deployment_id=request.deployment_id,
            subscription_id=request.subscription.id,
            model_group=request.model_group,
            attempt=slots.attempts,
            alternatives=request.alternatives,
        )
        slots.by_deployment[request.deployment_id] = reservation
        return reservation

    async def release(self, reservation: Reservation) -> bool:
        """The slot also ages out by its TTL, so a store that cannot be reached is not an error for the request."""
        slots = self._requests.get(reservation.request_id)
        if slots is None or reservation.token in slots.released:
            return False
        slots.released.add(reservation.token)
        self._untrack(reservation.token)
        try:
            await self._store.release(reservation.subscription_id, reservation.token)
        except Exception as error:  # noqa: BLE001
            verbose_proxy_logger.warning(
                "agentek_gateway slot of %s was not released (%s)",
                reservation.subscription_id,
                type(error).__name__,
            )
        return True

    async def extend(self, reservation: Reservation) -> bool:
        """Keeps the slot of a long stream alive past its TTL while the stream still delivers."""
        self._track(reservation.token, reservation.subscription_id)
        return await self._store.extend(
            reservation.subscription_id, reservation.token, self._ttl_s
        )

    async def release_request(self, request_id: str | None) -> None:
        slots = self._requests.get(request_id) if request_id else None
        if slots:
            await self._release_live(slots)

    async def finish(self, request_id: str | None) -> None:
        if not request_id:
            return
        await self.release_request(request_id)
        self._requests.discard(request_id)

    def find(
        self, request_id: str | None, deployment_id: str | None
    ) -> Reservation | None:
        slots = self._requests.get(request_id) if request_id else None
        return (
            slots.by_deployment.get(deployment_id) if slots and deployment_id else None
        )

    def active(self, request_id: str | None) -> Reservation | None:
        slots = self._requests.get(request_id) if request_id else None
        if not slots or not slots.by_deployment:
            return None
        return max(slots.by_deployment.values(), key=lambda held: held.attempt)

    def in_flight_here(self) -> Mapping[SubscriptionId, int]:
        """Requests this process has under way per subscription, counted from the moment a slot is asked for."""
        now = self._clock.now()
        if now - self._swept_at >= EXPIRED_SWEEP_INTERVAL_S:
            self._swept_at = now
            for token in [
                token
                for token, (_, expires_at) in self._live.items()
                if expires_at <= now
            ]:
                self._untrack(token)
        return self._here

    def _track(self, token: str, subscription_id: SubscriptionId) -> None:
        if token not in self._live:
            self._here[subscription_id] = self._here.get(subscription_id, 0) + 1
        self._live[token] = (subscription_id, self._clock.now() + self._ttl_s)

    def _untrack(self, token: str) -> None:
        entry = self._live.pop(token, None)
        if entry is not None:
            self._here[entry[0]] -= 1

    def size(self) -> int:
        return len(self._requests)

    async def _release_live(self, slots: _RequestSlots) -> None:
        for reservation in tuple(slots.by_deployment.values()):
            await self.release(reservation)

    def _open(self, request_id: str) -> _RequestSlots:
        slots = _RequestSlots()
        self._requests.put(request_id, slots)
        return slots
