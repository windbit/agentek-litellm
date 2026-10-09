import random
from typing import Protocol

from .config import ProviderTuning
from .model import SubscriptionState


class Rng(Protocol):
    def uniform(self, a: float, b: float, /) -> float: ...


BROKEN_JITTER_RANGE = (0.9, 1.1)


def next_probe_delay(
    state: SubscriptionState, tuning: ProviderTuning, rng: Rng | None = None
) -> float | None:
    source = rng or random.Random()
    match state:
        case SubscriptionState.HALF_OPEN:
            return source.uniform(0.0, tuning.half_open_probe_interval_s)
        case SubscriptionState.BROKEN:
            low, high = BROKEN_JITTER_RANGE
            return tuning.broken_probe_interval_s * source.uniform(low, high)
        case _:
            return None
