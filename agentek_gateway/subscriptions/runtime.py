from dataclasses import dataclass

from .gateway import GatewayParts, SubscriptionGateway, request_key_of
from .outcomes import OutcomeTracker


@dataclass(frozen=True, slots=True)
class SubscriptionRuntime:
    parts: GatewayParts
    gateway: SubscriptionGateway
    outcomes: OutcomeTracker

    def request_key(self, request: dict[str, object]) -> str | None:
        return request_key_of(request)


def assemble(parts: GatewayParts) -> SubscriptionRuntime:
    return SubscriptionRuntime(
        parts=parts,
        gateway=SubscriptionGateway(parts),
        outcomes=OutcomeTracker(parts),
    )


@dataclass(slots=True)
class RuntimeSlot:
    runtime: SubscriptionRuntime | None = None


GLOBAL_SLOT = RuntimeSlot()
