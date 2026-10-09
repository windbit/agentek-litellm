from prometheus_client import REGISTRY

from agentek_gateway.subscriptions.failures import SwitchReason
from agentek_gateway.subscriptions.model import (
    EgressInfo,
    Limits,
    Route,
    SubscriptionState as S,
    UsageRecord,
    Window,
)
from agentek_gateway.subscriptions.prometheus_telemetry import (
    PrometheusTelemetry,
    TelemetryLoop,
    TelemetryView,
)

from .builders import snapshot_of, state_record
from .conftest import make_subscription
from .plain import plain_runtime

NOW = 1_000_000.0
IP = "2001:db8::7"


def sample(name: str, **labels: str) -> float | None:
    return REGISTRY.get_sample_value(name, labels)


def view(*subscriptions, states=None, usage=None, in_flight=None, **extra):  # type: ignore[no-untyped-def]
    snapshot = snapshot_of(
        list(subscriptions), states=states, usage=usage, in_flight=in_flight
    )
    return TelemetryView(
        snapshot=snapshot,
        probe_times=extra.get("probe_times", {}),
        degraded=extra.get("degraded", frozenset()),
        egress=extra.get("egress", {}),
        now=NOW,
    )


def test_state_is_published_as_one_current_state_per_subscription() -> None:
    first = make_subscription("t1", name="metric-state-a")
    second = make_subscription("t2", name="metric-state-b")
    third = make_subscription("t3", name="metric-state-c", enabled=False)

    PrometheusTelemetry().publish(
        view(
            first, second, third, states={"t2": state_record(S.RATE_LIMITED, NOW + 600)}
        )
    )

    current = {
        name: [
            state
            for state in S
            if sample(
                "agentek_subscription_state",
                subscription=name,
                provider="chatgpt",
                state=state.value,
            )
            == 1
        ]
        for name in ("metric-state-a", "metric-state-b", "metric-state-c")
    }
    assert current == {
        "metric-state-a": [S.ACTIVE],
        "metric-state-b": [S.RATE_LIMITED],
        "metric-state-c": [S.DISABLED],
    }


def test_expired_block_is_published_as_half_open() -> None:
    subscription = make_subscription("t1", name="metric-expired")

    PrometheusTelemetry().publish(
        view(subscription, states={"t1": state_record(S.OVERLOADED, NOW - 1)})
    )

    assert (
        sample(
            "agentek_subscription_state",
            subscription="metric-expired",
            provider="chatgpt",
            state="HALF_OPEN",
        )
        == 1
    )


def test_windows_and_concurrent_requests_are_published_per_subscription() -> None:
    subscription = make_subscription("t1", name="metric-windows")
    usage = UsageRecord(Limits(Window(42.5, NOW + 100), Window(80.0, NOW + 9000)), NOW)

    PrometheusTelemetry().publish(
        view(subscription, usage={"t1": usage}, in_flight={"t1": 3})
    )

    assert (
        sample(
            "agentek_subscription_window_used_percent",
            subscription="metric-windows",
            window="five_hour",
        ),
        sample(
            "agentek_subscription_window_used_percent",
            subscription="metric-windows",
            window="weekly",
        ),
        sample(
            "agentek_subscription_window_reset_timestamp_seconds",
            subscription="metric-windows",
            window="weekly",
        ),
        sample(
            "agentek_subscription_in_flight_requests", subscription="metric-windows"
        ),
    ) == (42.5, 80.0, NOW + 9000, 3.0)


def test_working_subscriptions_are_counted_per_provider() -> None:
    subs = [
        make_subscription(
            f"w{index}",
            name=f"metric-working-{index}",
            provider="metric-prov",
            enabled=index != 2,
        )
        for index in range(4)
    ]

    PrometheusTelemetry().publish(
        view(
            *subs,
            states={
                "w0": state_record(S.BANNED),
                "w1": state_record(S.SOFT_LIMITED, NOW + 60),
            },
        )
    )

    assert sample("agentek_subscription_working", provider="metric-prov") == 2


def test_time_since_the_last_probe_pass_is_published_per_provider() -> None:
    subscription = make_subscription(
        "t1", name="metric-probe", provider="metric-probe-prov"
    )

    PrometheusTelemetry().publish(
        view(subscription, probe_times={"metric-probe-prov": NOW - 42})
    )

    assert (
        sample("agentek_subscription_seconds_since_probe", provider="metric-probe-prov")
        == 42
    )


def test_degraded_route_is_published_with_its_address_and_data_center() -> None:
    subscription = make_subscription(
        "t1", name="metric-route", provider="metric-route-prov"
    )
    route = Route("metric-route-prov", "eu")
    egress = {route: EgressInfo(IP, "FRA", NOW)}

    PrometheusTelemetry().publish(
        view(subscription, degraded=frozenset({route}), egress=egress)
    )

    labels = {
        "provider": "metric-route-prov",
        "egress": "eu",
        "egress_ip": IP,
        "colo": "FRA",
    }
    assert (
        sample("agentek_subscription_route_degraded", **labels),
        sample("agentek_subscription_egress_info", **labels),
    ) == (1, 1)


def test_healthy_route_is_published_as_not_degraded() -> None:
    subscription = make_subscription("t1", name="metric-ok", provider="metric-ok-prov")
    route = Route("metric-ok-prov", None)

    PrometheusTelemetry().publish(
        view(subscription, egress={route: EgressInfo(IP, "ARN", NOW)})
    )

    labels = {
        "provider": "metric-ok-prov",
        "egress": "",
        "egress_ip": IP,
        "colo": "ARN",
    }
    assert sample("agentek_subscription_route_degraded", **labels) == 0


def test_degraded_route_without_a_measured_egress_is_still_visible() -> None:
    route = Route("metric-unknown-prov", "eu")

    PrometheusTelemetry().publish(view(degraded=frozenset({route})))

    assert (
        sample(
            "agentek_subscription_route_degraded",
            provider="metric-unknown-prov",
            egress="eu",
            egress_ip="",
            colo="",
        )
        == 1
    )


def test_removed_subscription_disappears_from_the_next_publication() -> None:
    subscription = make_subscription("t1", name="metric-gone")
    telemetry = PrometheusTelemetry()
    telemetry.publish(view(subscription))

    telemetry.publish(view())

    assert (
        sample(
            "agentek_subscription_state",
            subscription="metric-gone",
            provider="chatgpt",
            state="ACTIVE",
        )
        is None
    )


def test_switches_are_counted_by_subscription_and_reason() -> None:
    subscription = make_subscription("t1", name="metric-switch")
    telemetry = PrometheusTelemetry()

    for _ in range(3):
        telemetry.switched(subscription, SwitchReason.LIMIT)
    telemetry.switched(subscription, SwitchReason.AUTH)

    assert (
        sample(
            "agentek_subscription_switches_total",
            subscription="metric-switch",
            reason="limit",
        ),
        sample(
            "agentek_subscription_switches_total",
            subscription="metric-switch",
            reason="auth",
        ),
    ) == (3, 1)


def test_account_identity_is_never_a_label() -> None:
    PrometheusTelemetry()
    labels = {
        label
        for metric in REGISTRY.collect()
        if metric.name.startswith("agentek_subscription")
        for item in metric.samples
        for label in item.labels
    }

    assert labels.isdisjoint({"email", "account", "account_id", "token"})


async def test_loop_publishes_what_the_store_holds_and_feeds_the_egress_book() -> None:
    plain = plain_runtime(["tl1"])
    subscription = make_subscription("tl1", name="metric-loop")
    plain.repo.put(subscription)
    await plain.runtime.parts.snapshot.refresh()
    await plain.store.mark_probed("chatgpt", plain.clock.now() - 7)
    route = Route("chatgpt", None)
    await plain.store.write_egress(route, EgressInfo(IP, "ARN", NOW))
    book = plain.runtime.parts.egress
    loop = TelemetryLoop(
        plain.runtime.parts.snapshot,
        plain.store,
        book,
        PrometheusTelemetry(),
        plain.clock,
    )

    await loop.publish_once()

    assert (
        sample("agentek_subscription_seconds_since_probe", provider="chatgpt"),
        book.note(route),
    ) == (7, f"egress_ip={IP} colo=ARN")


def test_closed_snapshot_publishes_no_working_subscriptions() -> None:
    from dataclasses import replace

    subscription = make_subscription(
        "t1", name="metric-closed", provider="metric-closed-prov"
    )
    closed = replace(view(subscription).snapshot, closed=True)

    PrometheusTelemetry().publish(replace(view(subscription), snapshot=closed))

    assert sample("agentek_subscription_working", provider="metric-closed-prov") == 0
