import heapq
from typing import Generic, TypeVar

from .clock import Clock

K = TypeVar("K")
V = TypeVar("V")

DEFAULT_MAX_ENTRIES = 50_000
COMPACT_FACTOR = 4
COMPACT_MIN_STALE = 1024


class ExpiringMap(Generic[K, V]):
    """Process-local map whose entries vanish after a TTL; the oldest go first when the cap is reached."""

    def __init__(
        self, clock: Clock, ttl_s: float, max_entries: int = DEFAULT_MAX_ENTRIES
    ) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._items: dict[K, tuple[float, int, V]] = {}
        self._expiry: list[tuple[float, int, K]] = []
        self._sequence = 0

    def put(self, key: K, value: V) -> None:
        self._sweep()
        expires_at = self._clock.now() + self._ttl_s
        self._sequence += 1
        self._items[key] = (expires_at, self._sequence, value)
        heapq.heappush(self._expiry, (expires_at, self._sequence, key))
        self._evict_overflow()
        self._compact()

    def get(self, key: K) -> V | None:
        entry = self._items.get(key)
        if entry is None or entry[0] <= self._clock.now():
            return None
        return entry[2]

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
            self._drop_head()

    def _evict_overflow(self) -> None:
        while len(self._items) > self._max_entries and self._expiry:
            self._drop_head()

    def _drop_head(self) -> None:
        _, sequence, key = heapq.heappop(self._expiry)
        entry = self._items.get(key)
        if entry is not None and entry[1] == sequence:
            del self._items[key]

    def _compact(self) -> None:
        stale = len(self._expiry) - len(self._items)
        if stale > COMPACT_MIN_STALE and len(self._expiry) > COMPACT_FACTOR * len(
            self._items
        ):
            self._expiry = [
                (expires_at, sequence, key)
                for key, (expires_at, sequence, _) in self._items.items()
            ]
            heapq.heapify(self._expiry)
