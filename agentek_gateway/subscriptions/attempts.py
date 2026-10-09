import heapq
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from .clock import Clock

REQUEST_ID_FIELD = "agentek_request_id"
DEFAULT_MAX_TRACKED_REQUESTS = 50_000


def issue_request_id() -> str:
    return uuid.uuid4().hex


def request_metadata(request_kwargs: Mapping[str, object]) -> Mapping[str, object]:
    for name in ("litellm_metadata", "metadata"):
        value = request_kwargs.get(name)
        if isinstance(value, Mapping):
            return value
    return {}


def stamp_request_id(request_data: dict[str, object]) -> str:
    """Writes a fresh server-side id into the request metadata, replacing any client-supplied value."""
    name = (
        "litellm_metadata"
        if isinstance(request_data.get("litellm_metadata"), dict)
        else "metadata"
    )
    container = request_data.get(name)
    if not isinstance(container, dict):
        container = {}
        request_data[name] = container
    request_id = issue_request_id()
    container[REQUEST_ID_FIELD] = request_id
    return request_id


def read_request_id(request_kwargs: Mapping[str, object]) -> str | None:
    value = request_metadata(request_kwargs).get(REQUEST_ID_FIELD)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    deployment_ids: frozenset[str]
    alternatives: int
    expires_at: float


class AttemptTracker:
    """Deployments already tried by one request, keyed by the id the plugin issued (never by litellm_call_id)."""

    def __init__(
        self,
        clock: Clock,
        ttl_s: float,
        max_requests: int = DEFAULT_MAX_TRACKED_REQUESTS,
    ) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._max_requests = max_requests
        self._records: dict[str, AttemptRecord] = {}
        self._expiry: list[tuple[float, str]] = []

    def attempted(self, request_id: str | None) -> frozenset[str]:
        record = self._live(request_id)
        return record.deployment_ids if record else frozenset()

    def alternatives(self, request_id: str | None) -> int:
        record = self._live(request_id)
        return record.alternatives if record else 0

    def record(self, request_id: str, deployment_id: str, alternatives: int) -> None:
        self.sweep()
        previous = self._live(request_id)
        tried = previous.deployment_ids if previous else frozenset()
        expires_at = self._clock.now() + self._ttl_s
        self._records[request_id] = AttemptRecord(
            deployment_ids=tried | {deployment_id},
            alternatives=alternatives,
            expires_at=expires_at,
        )
        heapq.heappush(self._expiry, (expires_at, request_id))
        self._evict_overflow()

    def finish(self, request_id: str | None) -> None:
        if request_id:
            self._records.pop(request_id, None)

    def sweep(self) -> None:
        now = self._clock.now()
        for request_id in [
            rid for rid, record in self._records.items() if record.expires_at <= now
        ]:
            del self._records[request_id]

    def size(self) -> int:
        self.sweep()
        return len(self._records)

    def _live(self, request_id: str | None) -> AttemptRecord | None:
        record = self._records.get(request_id) if request_id else None
        if record is None or record.expires_at <= self._clock.now():
            return None
        return record

    def _evict_overflow(self) -> None:
        overflow = len(self._records) - self._max_requests
        if overflow <= 0:
            return
        oldest = sorted(self._records, key=lambda rid: self._records[rid].expires_at)[
            :overflow
        ]
        for request_id in oldest:
            del self._records[request_id]
