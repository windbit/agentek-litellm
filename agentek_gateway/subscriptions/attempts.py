import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from .clock import Clock
from .expiring import ExpiringMap

REQUEST_ID_FIELD = "agentek_request_id"
DEFAULT_MAX_TRACKED_REQUESTS = 50_000


def issue_request_id() -> str:
    return uuid.uuid4().hex


def request_metadata(request_kwargs: Mapping[str, object]) -> Mapping[str, object]:
    """The container the router writes into: litellm_metadata when the request carries one, else metadata."""
    preferred = (
        "litellm_metadata" if "litellm_metadata" in request_kwargs else "metadata"
    )
    for name in (preferred, "metadata", "litellm_metadata"):
        value = request_kwargs.get(name)
        if isinstance(value, Mapping) and value:
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
    started_at: float


class AttemptTracker:
    """Deployments already tried by one request, keyed by the id the plugin issued (never by litellm_call_id)."""

    def __init__(
        self,
        clock: Clock,
        ttl_s: float,
        max_requests: int = DEFAULT_MAX_TRACKED_REQUESTS,
    ) -> None:
        self._clock = clock
        self._records: ExpiringMap[str, AttemptRecord] = ExpiringMap(
            clock, ttl_s, max_requests
        )

    def attempted(self, request_id: str | None) -> frozenset[str]:
        record = self._records.get(request_id) if request_id else None
        return record.deployment_ids if record else frozenset()

    def age_s(self, request_id: str | None) -> float:
        record = self._records.get(request_id) if request_id else None
        return self._clock.now() - record.started_at if record else 0.0

    def alternatives(self, request_id: str | None) -> int:
        record = self._records.get(request_id) if request_id else None
        return record.alternatives if record else 0

    def record(self, request_id: str, deployment_id: str, alternatives: int) -> None:
        previous = self._records.get(request_id)
        tried = previous.deployment_ids if previous else frozenset()
        started_at = previous.started_at if previous else self._clock.now()
        self._records.put(
            request_id,
            AttemptRecord(tried | {deployment_id}, alternatives, started_at),
        )

    def finish(self, request_id: str | None) -> None:
        if request_id:
            self._records.discard(request_id)

    def size(self) -> int:
        return len(self._records)
