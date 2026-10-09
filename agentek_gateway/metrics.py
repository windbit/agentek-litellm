from collections.abc import Sequence
from typing import Literal

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

MetricKind = Literal["counter", "gauge", "histogram"]
Metric = Counter | Gauge | Histogram
COUNTER_SUFFIX = "_total"


def get_or_create_metric(
    kind: MetricKind, name: str, documentation: str, labels: Sequence[str] = ()
) -> Metric:
    """Registers the metric once per process; a repeated call returns the registered collector."""
    existing = REGISTRY._names_to_collectors.get(
        name
    ) or REGISTRY._names_to_collectors.get(name + COUNTER_SUFFIX)
    if isinstance(existing, (Counter, Gauge, Histogram)):
        return existing
    match kind:
        case "counter":
            return Counter(name, documentation, labels)
        case "gauge":
            return Gauge(name, documentation, labels)
        case "histogram":
            return Histogram(name, documentation, labels)
