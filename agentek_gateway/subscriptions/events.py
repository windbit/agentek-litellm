from dataclasses import dataclass
from enum import StrEnum

from .model import Limits


class LimitWindow(StrEnum):
    FIVE_HOUR = "five_hour"
    WEEKLY = "weekly"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LimitExhausted:
    window: LimitWindow
    reset_at: float | None


@dataclass(frozen=True, slots=True)
class LimitsObserved:
    limits: Limits


@dataclass(frozen=True, slots=True)
class Overloaded:
    pass


@dataclass(frozen=True, slots=True)
class ProbeFailed:
    pass


@dataclass(frozen=True, slots=True)
class ProbeSucceeded:
    limits: Limits | None = None


@dataclass(frozen=True, slots=True)
class Unauthorized:
    pass


@dataclass(frozen=True, slots=True)
class RefreshSucceeded:
    pass


@dataclass(frozen=True, slots=True)
class TokenRevoked:
    pass


@dataclass(frozen=True, slots=True)
class AccountDeactivated:
    pass


@dataclass(frozen=True, slots=True)
class Expired:
    pass


@dataclass(frozen=True, slots=True)
class OperatorDisabled:
    pass


@dataclass(frozen=True, slots=True)
class OperatorEnabled:
    pass


@dataclass(frozen=True, slots=True)
class Reauthorized:
    pass


@dataclass(frozen=True, slots=True)
class Restored:
    pass


Event = (
    LimitExhausted
    | LimitsObserved
    | Overloaded
    | ProbeFailed
    | ProbeSucceeded
    | Unauthorized
    | RefreshSucceeded
    | TokenRevoked
    | AccountDeactivated
    | Expired
    | OperatorDisabled
    | OperatorEnabled
    | Reauthorized
    | Restored
)
