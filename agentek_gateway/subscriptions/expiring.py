import heapq
from typing import Generic, TypeVar

from .clock import Clock

K = TypeVar("K")
V = TypeVar("V")

DEFAULT_MAX_ENTRIES = 50_000


class ExpiringMap(Generic[K, V]):
    """Process-local map whose entries vanish after a TTL; the oldest go first when the cap is reached."""

    def __init__(
        self, clock: Clock, ttl_s: float, max_entries: int = DEFAULT_MAX_ENTRIES
    ) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._items: dict[K, tuple[float, V]] = {}
        self._expiry: list[tuple[float, int, K]] = []
        self._sequence = 0

    def put(self, key: K, value: V) -> None:
        self._sweep()
        expires_at = self._clock.now() + self._ttl_s
        self._items[key] = (expires_at, value)
        self._sequence += 1
        heapq.heappush(self._expiry, (expires_at, self._sequence, key))
        self._evict_overflow()

    def get(self, key: K) -> V | None:
        entry = self._items.get(key)
        if entry is None or entry[0] <= self._clock.now():
            return None
        return entry[1]

    def pop(self, key: K) -> V | None:
        value = self.get(key)
        self._items.pop(key, None)
        return value

    def discard(self, key: K) -> None:
        self._items.pop(key, None)

    def __contains__(self, key: K) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        self._sweep()
        return len(self._items)

    def _sweep(self) -> None:
        now = self._clock.now()
        while self._expiry and self._expiry[0][0] <= now:
            expires_at, _, key = heapq.heappop(self._expiry)
            entry = self._items.get(key)
            if entry is not None and entry[0] <= expires_at:
                del self._items[key]

    def _evict_overflow(self) -> None:
        while len(self._items) > self._max_entries and self._expiry:
            _, _, key = heapq.heappop(self._expiry)
            self._items.pop(key, None)
