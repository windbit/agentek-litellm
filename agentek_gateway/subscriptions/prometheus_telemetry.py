import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from prometheus_client import Counter, Gauge

from litellm._logging import verbose_proxy_logger

from ..metrics import get_or_create_metric
from .clock import Clock
from .egress import EgressBook
from .failures import SwitchReason
from .model import (
    EgressInfo,
    Route,
    Subscription,
    SubscriptionState,
    Window,
    effective_state,
    is_working,
)
from .ports import StateStore
from .selection import Snapshot
from .snapshot import SnapshotCache

PUBLISH_INTERVAL_S = 5.0
WINDOW_FIVE_HOUR = "five_hour"
WINDOW_WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class TelemetryView:
    snapshot: Snapshot
    probe_times: Mapping[str, float]
    degraded: frozenset[Route]
    egress: Mapping[Route, EgressInfo]
    now: float


class PrometheusTelemetry:
    """Gateway metrics for subscriptions; subscription names are labels, account e-mails never are."""

    def __init__(self) -> None:
        self._switches = _counter(
            "agentek_subscription_switches",
            "Attempts that left a subscription, by reason",
            ("subscription", "reason"),
        )
        self._state = _gauge(
            "agentek_subscription_state",
            "1 for the current state of a subscription",
            ("subscription", "provider", "state"),
        )
        self._window_used = _gauge(
            "agentek_subscription_window_used_percent",
            "Last observed usage of a limit window",
            ("subscription", "window"),
        )
        self._window_reset = _gauge(
            "agentek_subscription_window_reset_timestamp_seconds",
            "When a limit window resets",
            ("subscription", "window"),
        )
        self._in_flight = _gauge(
            "agentek_subscription_in_flight_requests",
            "Concurrent requests on a subscription",
            ("subscription",),
        )
        self._working = _gauge(
            "agentek_subscription_working",
            "Subscriptions of a provider that can take requests",
            ("provider",),
        )
        self._probe_age = _gauge(
            "agentek_subscription_seconds_since_probe",
            "Seconds since the leader last probed a provider",
            ("provider",),
        )
        self._degraded = _gauge(
            "agentek_subscription_route_degraded",
            "1 while most subscriptions on one egress fail together",
            ("provider", "egress", "egress_ip", "colo"),
        )
        self._egress = _gauge(
            "agentek_subscription_egress_info",
            "Egress address and provider data center of a route",
            ("provider", "egress", "egress_ip", "colo"),
        )

    def switched(self, subscription: Subscription, reason: SwitchReason) -> None:
        self._switches.labels(subscription.name, reason.value).inc()

    def publish(self, view: TelemetryView) -> None:
        for gauge in (
            self._state,
            self._window_used,
            self._window_reset,
            self._in_flight,
            self._working,
            self._probe_age,
            self._degraded,
            self._egress,
        ):
            gauge.clear()
        snapshot = view.snapshot
        for subscription in snapshot.subscriptions.values():
            self._publish_subscription(subscription, view)
        self._publish_providers(view)
        self._publish_routes(view)

    def _publish_subscription(
        self, subscription: Subscription, view: TelemetryView
    ) -> None:
        snapshot = view.snapshot
        record = snapshot.states.get(subscription.id)
        current = (
            effective_state(record, view.now) if record else SubscriptionState.ACTIVE
        )
        if not subscription.enabled:
            current = SubscriptionState.DISABLED
        for state in SubscriptionState:
            self._state.labels(
                subscription.name, subscription.provider, state.value
            ).set(1 if state is current else 0)
        self._in_flight.labels(subscription.name).set(
            snapshot.in_flight.get(subscription.id, 0)
        )
        usage = snapshot.usage.get(subscription.id)
        if usage:
            for label, window in (
                (WINDOW_FIVE_HOUR, usage.limits.five_hour),
                (WINDOW_WEEKLY, usage.limits.weekly),
            ):
                self._publish_window(subscription, label, window)

    def _publish_window(
        self, subscription: Subscription, label: str, window: Window | None
    ) -> None:
        if window is None:
            return
        self._window_used.labels(subscription.name, label).set(window.used_percent)
        self._window_reset.labels(subscription.name, label).set(window.reset_at)

    def _publish_providers(self, view: TelemetryView) -> None:
        snapshot = view.snapshot
        working: dict[str, int] = {}
        for subscription in snapshot.subscriptions.values():
            working.setdefault(subscription.provider, 0)
            record = snapshot.states.get(subscription.id)
            current = (
                effective_state(record, view.now)
                if record
                else SubscriptionState.ACTIVE
            )
            if subscription.enabled and not snapshot.closed and is_working(current):
                working[subscription.provider] += 1
        for provider, count in working.items():
            self._working.labels(provider).set(count)
            probed_at = view.probe_times.get(provider)
            if probed_at is not None:
                self._probe_age.labels(provider).set(max(0.0, view.now - probed_at))

    def _publish_routes(self, view: TelemetryView) -> None:
        for route, info in view.egress.items():
            labels = (route.provider, route.egress or "", info.ip, info.colo)
            self._egress.labels(*labels).set(1)
            self._degraded.labels(*labels).set(1 if route in view.degraded else 0)
        for route in view.degraded - set(view.egress):
            self._degraded.labels(route.provider, route.egress or "", "", "").set(1)


class TelemetryLoop:
    """Reads what the replicas share and republishes it as metrics; also feeds the egress book used by logs."""

    def __init__(
        self,
        snapshot: SnapshotCache,
        store: StateStore,
        book: EgressBook,
        telemetry: PrometheusTelemetry,
        clock: Clock,
        interval_s: float = PUBLISH_INTERVAL_S,
    ) -> None:
        self._interval_s = interval_s
        self._snapshot = snapshot
        self._store = store
        self._book = book
        self._telemetry = telemetry
        self._clock = clock

    async def run(self) -> None:
        while True:
            try:
                await self.publish_once()
            except Exception:  # noqa: BLE001
                verbose_proxy_logger.exception("agentek_gateway metrics refresh failed")
            await asyncio.sleep(self._interval_s)

    async def publish_once(self) -> None:
        snapshot = self._snapshot.current
        if snapshot is None:
            return
        egress = await self._store.read_egress()
        self._book.replace(egress)
        self._telemetry.publish(
            TelemetryView(
                snapshot=snapshot,
                probe_times=await self._store.read_probe_times(),
                degraded=await self._store.degraded_routes(),
                egress=egress,
                now=self._clock.now(),
            )
        )


def _gauge(name: str, documentation: str, labels: tuple[str, ...]) -> Gauge:
    metric = get_or_create_metric("gauge", name, documentation, labels)
    assert isinstance(metric, Gauge)
    return metric


def _counter(name: str, documentation: str, labels: tuple[str, ...]) -> Counter:
    metric = get_or_create_metric("counter", name, documentation, labels)
    assert isinstance(metric, Counter)
    return metric
