from collections.abc import Sequence
from typing import Literal

from prometheus_client import Counter, Gauge, Histogram

MetricKind = Literal["counter", "gauge", "histogram"]
Metric = Counter | Gauge | Histogram

_CREATED: dict[str, Metric] = {}


def get_or_create_metric(
    kind: MetricKind, name: str, documentation: str, labels: Sequence[str] = ()
) -> Metric:
    """Registers the metric once per process; a repeated call returns the registered collector."""
    existing = _CREATED.get(name)
    if existing is not None:
        return existing
    match kind:
        case "counter":
            metric: Metric = Counter(name, documentation, labels)
        case "gauge":
            metric = Gauge(name, documentation, labels)
        case "histogram":
            metric = Histogram(name, documentation, labels)
    _CREATED[name] = metric
    return metric
