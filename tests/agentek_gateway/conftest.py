import pytest

from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.memory import (
    InMemorySubscriptionRepo,
    InMemoryStateStore,
)
from agentek_gateway.subscriptions.model import Subscription
from agentek_gateway.subscriptions.service import StateService
from agentek_gateway.subscriptions.signals import SignalProcessor

START = 1_000_000.0


class FakeClock:
    def __init__(self, start: float = START) -> None:
        self.current = start

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def make_subscription(sub_id: str, **overrides: object) -> Subscription:
    fields: dict[str, object] = {
        "id": sub_id,
        "provider": "chatgpt",
        "name": f"name-{sub_id}",
        "credential_name": f"cred-{sub_id}",
    }
    return Subscription(**{**fields, **overrides})  # type: ignore[arg-type]


class Harness:
    def __init__(
        self, subscriptions: list[Subscription], config: GatewayConfig | None = None
    ) -> None:
        self.clock = FakeClock()
        self.config = config or GatewayConfig()
        self.store = InMemoryStateStore(self.clock)
        self.repo = InMemorySubscriptionRepo(subscriptions)
        self.states = StateService(self.store, self.clock, self.config)
        self.signals = SignalProcessor(
            self.store, self.repo, self.states, self.clock, self.config
        )
        self.subscriptions = {sub.id: sub for sub in subscriptions}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def harness_four() -> Harness:
    return Harness(
        [make_subscription(f"s{index}", egress="eu") for index in range(1, 5)]
    )
