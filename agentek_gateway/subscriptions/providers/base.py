from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ..events import LimitWindow
from ..model import Limits


@dataclass(frozen=True, slots=True)
class LimitReached:
    window: LimitWindow
    reset_at: float | None


@dataclass(frozen=True, slots=True)
class AuthRejected:
    pass


@dataclass(frozen=True, slots=True)
class AccountBanned:
    pass


@dataclass(frozen=True, slots=True)
class RequestRejected:
    status: int


@dataclass(frozen=True, slots=True)
class ModelNotSupported:
    model: str | None


@dataclass(frozen=True, slots=True)
class Unclassified:
    immediate: bool
    recognized: bool


ErrorClass = (
    LimitReached
    | AuthRejected
    | AccountBanned
    | RequestRejected
    | ModelNotSupported
    | Unclassified
)

Headers = Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RefreshedTokens:
    access_token: str
    refresh_token: str
    id_token: str | None
    expires_at: float | None


@dataclass(frozen=True, slots=True)
class RefreshRejected:
    permanent: bool


RefreshOutcome = RefreshedTokens | RefreshRejected


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    error: ErrorClass | None
    limits: Limits | None


class SubscriptionProvider(Protocol):
    id: str

    def parse_limits(
        self, headers: Headers, body: Mapping[str, object] | None, *, now: float
    ) -> Limits | None: ...

    def classify_error(
        self, status: int, headers: Headers, body: str, *, now: float
    ) -> ErrorClass: ...
