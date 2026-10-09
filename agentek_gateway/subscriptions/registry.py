from collections.abc import Mapping
from dataclasses import dataclass

from .clock import Clock
from .expiring import ExpiringMap

AttemptKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class UpstreamReply:
    status: int
    headers: Mapping[str, str]


class AttemptRegistry:
    """What each failure source has already handled, keyed by (request id, deployment id)."""

    def __init__(self, clock: Clock, ttl_s: float) -> None:
        self._observed: ExpiringMap[AttemptKey, bool] = ExpiringMap(clock, ttl_s)
        self._streams: ExpiringMap[AttemptKey, bool] = ExpiringMap(clock, ttl_s)
        self._upstream: ExpiringMap[str, UpstreamReply] = ExpiringMap(clock, ttl_s)

    def mark_handled(self, key: AttemptKey) -> None:
        self._observed.put(key, True)

    def was_handled(self, key: AttemptKey) -> bool:
        return key in self._observed

    def mark_streaming(self, key: AttemptKey) -> None:
        self._streams.put(key, True)

    def is_streaming(self, key: AttemptKey) -> bool:
        return key in self._streams

    def remember_upstream(self, request_id: str, reply: UpstreamReply) -> None:
        self._upstream.put(request_id, reply)

    def take_upstream(self, request_id: str) -> UpstreamReply | None:
        return self._upstream.pop(request_id)
