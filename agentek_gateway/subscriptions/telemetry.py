from typing import Protocol

from .failures import SwitchReason
from .model import Subscription


class Telemetry(Protocol):
    def switched(self, subscription: Subscription, reason: SwitchReason) -> None: ...

    def failed(self, subscription: Subscription, reason: SwitchReason) -> None: ...


class NullTelemetry:
    def switched(self, subscription: Subscription, reason: SwitchReason) -> None:
        return None

    def failed(self, subscription: Subscription, reason: SwitchReason) -> None:
        return None
