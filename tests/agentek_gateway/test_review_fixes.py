import asyncio
import json
import math
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from litellm.litellm_core_utils.litellm_logging import StandardLoggingPayloadSetup
from litellm.llms.base_llm.chat.transformation import BaseLLMException
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig
from litellm.proxy._types import LitellmUserRoles

from agentek_gateway.api import require_proxy_admin
from agentek_gateway.startup import (
    GatewayState,
    Plugin,
    register_callbacks,
    start_gateway,
)
from agentek_gateway.subscriptions.attempts import (
    REQUEST_ID_FIELD,
    AttemptTracker,
    read_request_id,
    stamp_request_id,
)
from agentek_gateway.subscriptions.config import GatewayConfig, load_config
from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
from agentek_gateway.subscriptions.events import SeriesCleared, Succeeded
from agentek_gateway.subscriptions.filtering import FilterContext, filter_deployments
from agentek_gateway.subscriptions.machine import transition
from agentek_gateway.subscriptions.model import (
    Limits,
    Route,
    SubscriptionState as S,
    Window,
)
from agentek_gateway.subscriptions.policy import (
    KeySubjects,
    Policy,
    key_subjects_from_metadata,
)
from agentek_gateway.subscriptions.providers.base import Unclassified
from agentek_gateway.subscriptions.providers.chatgpt_limits import (
    limits_from_usage_payload,
)
from agentek_gateway.subscriptions.providers.chatgpt_json import number_of
from agentek_gateway.subscriptions.providers.observer import (
    AttemptContext,
    attempt_scope,
    current_attempt,
    install_error_observer,
    uninstall_error_observer,
)
from agentek_gateway.subscriptions.providers.redact import (
    LOG_BODY_LIMIT_BYTES,
    redact_for_log,
)
from agentek_gateway.subscriptions.selection import (
    Candidate,
    Exhausted,
    Kept,
    SelectionRequest,
    select,
)
from agentek_gateway.subscriptions.stickiness import (
    SESSION_ID_PARAM,
    session_id_for,
    with_session_id,
)

from .builders import MODEL, NOW, deployments_for, snapshot_of, state_record, subject
from .conftest import FakeClock, Harness, make_subscription
from .test_machine import TUNING, record
from .test_startup import FakeHost, Marker

HOUR = 3600.0


async def state_of(harness: Harness, sub_id: str) -> S | None:
    stored = await harness.store.read_state(sub_id)
    return stored.state if stored else None


def four_on_one_route(**overrides: object) -> Harness:
    return Harness(
        [
            make_subscription(f"s{index}", egress="eu", **overrides)
            for index in range(1, 5)
        ]
    )


# X1, X2, X3, m8: the shared-cause picture


async def test_two_of_four_failing_once_each_mark_the_route_without_a_series() -> None:
    harness = four_on_one_route()
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s1"], immediate=False
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s2"], immediate=False
    )

    assert await harness.store.degraded_routes() == frozenset({Route("chatgpt", "eu")})


async def test_blocks_made_before_the_threshold_are_lifted_when_it_is_reached() -> None:
    harness = four_on_one_route()
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["s1"], immediate=False
        )
    assert await state_of(harness, "s1") is S.OVERLOADED
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s2"], immediate=False
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s3"], immediate=False
    )

    assert await state_of(harness, "s1") is S.ACTIVE


async def test_a_block_from_before_the_window_is_not_lifted() -> None:
    harness = Harness(
        [make_subscription(f"s{index}", egress="eu") for index in range(1, 5)],
        GatewayConfig(defaults={"overload_base_s": 900}),  # type: ignore[arg-type]
    )
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["s1"], immediate=False
        )
    harness.clock.advance(300)
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s2"], immediate=False
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s3"], immediate=False
    )

    assert await state_of(harness, "s1") is S.OVERLOADED


async def test_disabled_peers_stay_out_of_the_denominator() -> None:
    harness = Harness(
        [make_subscription(name, egress="eu") for name in ("a", "b", "c")]
        + [
            make_subscription(name, egress="eu", enabled=False)
            for name in ("d", "e", "f")
        ]
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["b"], immediate=False
    )
    for _ in range(3):
        await harness.signals.on_unclassified_error(
            harness.subscriptions["a"], immediate=False
        )

    assert (await harness.store.degraded_routes(), await state_of(harness, "a")) == (
        frozenset({Route("chatgpt", "eu")}),
        None,
    )


async def test_operator_disabled_state_also_leaves_the_denominator() -> None:
    harness = Harness(
        [
            make_subscription("a", egress="eu"),
            make_subscription("b", egress="eu"),
            make_subscription("c", egress="eu"),
        ]
    )
    from agentek_gateway.subscriptions.events import OperatorDisabled

    await harness.states.apply(harness.subscriptions["c"], OperatorDisabled())
    await harness.signals.on_unclassified_error(
        harness.subscriptions["b"], immediate=False
    )
    await harness.signals.on_unclassified_error(
        harness.subscriptions["a"], immediate=False
    )

    assert await harness.store.degraded_routes() == frozenset({Route("chatgpt", "eu")})


async def test_successes_between_errors_do_not_hide_the_shared_cause() -> None:
    harness = four_on_one_route()
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s1"], immediate=False
    )
    await harness.signals.on_success(harness.subscriptions["s1"])
    await harness.signals.on_unclassified_error(
        harness.subscriptions["s2"], immediate=False
    )
    await harness.signals.on_success(harness.subscriptions["s2"])

    assert await harness.store.degraded_routes() == frozenset({Route("chatgpt", "eu")})


# X4, X5


async def test_success_without_limits_keeps_a_soft_limit() -> None:
    harness = Harness([make_subscription("a")])
    sub = harness.subscriptions["a"]
    await harness.signals.on_success(
        sub, Limits(weekly=Window(97, NOW_OF(harness) + HOUR))
    )
    assert await state_of(harness, "a") is S.SOFT_LIMITED

    await harness.signals.on_success(sub)
    await harness.signals.on_success(sub, Limits())

    assert await state_of(harness, "a") is S.SOFT_LIMITED


def NOW_OF(harness: Harness) -> float:
    return harness.clock.now()


async def test_success_resets_the_overload_streak_between_bursts() -> None:
    harness = Harness([make_subscription("a")])
    sub = harness.subscriptions["a"]
    await harness.signals.on_unclassified_error(sub, immediate=True)
    await harness.signals.on_success(sub)
    harness.clock.advance(61)

    await harness.signals.on_unclassified_error(sub, immediate=True)

    stored = await harness.store.read_state("a")
    assert (stored.overload_streak, stored.until - harness.clock.now()) == (1, 60)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("state", "streak", "expected_streak", "expected_changed"),
    [
        (S.ACTIVE, 2, 0, True),
        (S.SOFT_LIMITED, 2, 0, True),
        (S.OVERLOADED, 2, 0, True),
        (S.ACTIVE, 0, 0, False),
        (S.BROKEN, 5, 5, False),
        (S.RATE_LIMITED, 2, 2, False),
    ],
)
def test_succeeded_event(
    state: S, streak: int, expected_streak: int, expected_changed: bool
) -> None:
    result = transition(
        record(state, until=NOW + HOUR, streak=streak), Succeeded(), NOW, TUNING
    )

    assert (result.record.overload_streak, result.changed, result.record.state) == (
        expected_streak,
        expected_changed,
        state,
    )


NOW = 1_000_000.0


@pytest.mark.parametrize(
    ("state", "source_series", "entered_ago", "expected"),
    [
        (S.OVERLOADED, True, 10, S.ACTIVE),
        (S.OVERLOADED, True, 500, S.OVERLOADED),
        (S.OVERLOADED, False, 10, S.OVERLOADED),
        (S.RATE_LIMITED, True, 10, S.RATE_LIMITED),
    ],
)
def test_series_cleared_lifts_only_recent_series_blocks(
    state: S, source_series: bool, entered_ago: float, expected: S
) -> None:
    from agentek_gateway.subscriptions.model import (
        SignalSource,
        StateReason,
        StateRecord,
    )

    source = SignalSource.SERIES if source_series else SignalSource.PROBE
    current = StateRecord(
        state,
        4,
        NOW - entered_ago,
        NOW + HOUR,
        StateReason.UNCLASSIFIED_SERIES,
        source,
        2,
    )

    result = transition(current, SeriesCleared(NOW - 120), NOW, TUNING)

    assert (result.record.state, result.record.overload_streak) == (
        expected,
        1 if expected is S.ACTIVE else 2,
    )


# X9, X16, X11


def test_alternatives_count_the_next_tier_and_shared_deployments() -> None:
    subs = [make_subscription("own", priority=1), make_subscription("shared")]
    policy = Policy(bindings={"own": frozenset({subject("space", "s1")})})
    snapshot = snapshot_of(subs, policy=policy)
    candidates = (*deployments_for("own", "shared"), Candidate("deepseek-1"))

    outcome = select(
        SelectionRequest(MODEL, KeySubjects(space=subject("space", "s1"))),
        candidates,
        snapshot,
        NOW,
    )

    assert isinstance(outcome, Kept) and outcome.alternatives == 2


def test_alternatives_ignore_shared_deployments_that_already_failed() -> None:
    subs = [make_subscription("a")]
    request = SelectionRequest(MODEL, excluded_deployment_ids=frozenset({"deepseek-1"}))

    outcome = select(
        request,
        (*deployments_for("a"), Candidate("deepseek-1")),
        snapshot_of(subs),
        NOW,
    )

    assert isinstance(outcome, Kept) and outcome.alternatives == 0


def test_recovery_time_ignores_the_soft_limit_of_a_working_subscription() -> None:
    subs = [make_subscription("a"), make_subscription("b")]
    states = {
        "a": state_record(S.SOFT_LIMITED, NOW + 9 * HOUR),
        "b": state_record(S.RATE_LIMITED, NOW + HOUR),
    }
    request = SelectionRequest(
        MODEL, excluded_deployment_ids=frozenset({f"sub:a:{MODEL}"})
    )

    outcome = select(
        request, deployments_for("a", "b"), snapshot_of(subs, states=states), NOW
    )

    assert outcome == Exhausted(NOW + 9 * HOUR)


def test_selection_scales_to_the_largest_group() -> None:
    import time

    subs = [make_subscription(f"s{index:04d}") for index in range(1800)]
    snapshot = snapshot_of(subs)
    candidates = deployments_for(*[sub.id for sub in subs])

    started = time.perf_counter()
    select(SelectionRequest(MODEL), candidates, snapshot, NOW)

    assert time.perf_counter() - started < 0.05


# filter wiring (T)


def run_filter(subs, request_kwargs, attempted=frozenset(), retry_after=10):  # type: ignore[no-untyped-def]
    snapshot = snapshot_of(subs)
    deployments = [
        {"model_info": {"id": f"sub:{sub.id}:{MODEL}"}, "litellm_params": {}}
        for sub in subs
    ]
    context = FilterContext(request_kwargs, attempted, None, NOW, retry_after)
    return filter_deployments(MODEL, deployments, snapshot, context)


def test_router_excluded_ids_are_honored() -> None:
    subs = [make_subscription("a"), make_subscription("b")]

    result = run_filter(subs, {"_excluded_deployment_ids": [f"sub:a:{MODEL}"]})

    assert result.chosen.subscription_id == "b"  # type: ignore[union-attr]


def test_plugin_attempts_are_honored_together_with_router_excluded_ids() -> None:
    subs = [make_subscription("a"), make_subscription("b"), make_subscription("c")]

    result = run_filter(
        subs,
        {"_excluded_deployment_ids": [f"sub:a:{MODEL}"]},
        attempted=frozenset({f"sub:b:{MODEL}"}),
    )

    assert result.chosen.subscription_id == "c"  # type: ignore[union-attr]


def test_retry_after_comes_from_the_caller() -> None:
    subs = [make_subscription("a")]

    with pytest.raises(NoAvailableSubscriptionsError) as raised:
        run_filter(subs, {}, attempted=frozenset({f"sub:a:{MODEL}"}), retry_after=25)

    assert raised.value.headers == {"retry-after": "25"}


def test_key_labels_are_read_from_either_metadata_container() -> None:
    labels = {"user_api_key_metadata": {"agentek_subjects": {"space": "s1"}}}
    subs = [make_subscription("open"), make_subscription("closed", priority=1)]
    policy_kwargs = {"litellm_metadata": labels}

    result = run_filter(subs, policy_kwargs)

    assert result.chosen.subscription_id == "closed"  # type: ignore[union-attr]


def test_bool_label_is_not_a_subject() -> None:
    assert key_subjects_from_metadata({"agentek_subjects": {"employee": True}}) is None


def test_no_capacity_error_carries_a_429_response() -> None:
    assert NoAvailableSubscriptionsError("m", None, 10).response.status_code == 429


# attempts


def test_expired_attempts_are_dropped_in_expiry_order() -> None:
    clock = FakeClock()
    attempts = AttemptTracker(clock, 100.0, max_requests=1000)
    for index in range(50):
        attempts.record(f"r{index}", "dep", alternatives=0)
        clock.advance(1)
    clock.advance(60)

    assert attempts.size() == 39


def test_overflow_drops_the_oldest_requests() -> None:
    clock = FakeClock()
    attempts = AttemptTracker(clock, 1000.0, max_requests=2)
    for name in ("old", "mid", "new"):
        attempts.record(name, "dep", alternatives=0)
        clock.advance(1)

    assert (attempts.attempted("old"), attempts.attempted("new")) == (
        frozenset(),
        frozenset({"dep"}),
    )


def test_stamping_replaces_a_client_supplied_request_id() -> None:
    data: dict[str, object] = {"metadata": {REQUEST_ID_FIELD: "client-chosen"}}

    issued = stamp_request_id(data)

    assert (issued, read_request_id(data)) == (
        read_request_id(data),
        issued,
    ) and issued != "client-chosen"


def test_stamping_creates_the_container_and_prefers_litellm_metadata() -> None:
    chat: dict[str, object] = {}
    responses: dict[str, object] = {"litellm_metadata": {}}

    stamp_request_id(chat)
    stamp_request_id(responses)

    assert (REQUEST_ID_FIELD in chat["metadata"], REQUEST_ID_FIELD in responses["litellm_metadata"]) == (True, True)  # type: ignore[operator]


# M1


def test_session_id_does_not_replace_the_trace_id() -> None:
    kwargs = with_session_id({"prompt_cache_key": "chat-1"}) or {}
    trace_id = StandardLoggingPayloadSetup._get_standard_logging_payload_trace_id(
        SimpleNamespace(litellm_trace_id="trace-from-litellm"),
        {"metadata": {}, **kwargs},
    )

    assert (SESSION_ID_PARAM, trace_id) == ("chatgpt_session_id", "trace-from-litellm")


def test_client_session_id_survives_next_to_the_plugin_one() -> None:
    kwargs = (
        with_session_id(
            {"prompt_cache_key": "chat-1", "litellm_session_id": "client-session"}
        )
        or {}
    )
    trace_id = StandardLoggingPayloadSetup._get_standard_logging_payload_trace_id(
        SimpleNamespace(litellm_trace_id="x"), kwargs
    )

    assert trace_id == "client-session"


def test_provider_prefers_the_plugin_session_over_the_client_one() -> None:
    params = {
        "litellm_credential_name": "cred",
        "chatgpt_auth": {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_at": 9_999_999_999,
            "account_id": "a",
        },
        "litellm_session_id": "client-session",
        "chatgpt_session_id": session_id_for("chat-1"),
    }

    headers = ChatGPTResponsesAPIConfig().validate_environment({}, "m", params)  # type: ignore[arg-type]

    assert headers["session_id"] == session_id_for("chat-1")


# providers


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), "nan", "inf", "-inf", True]
)
def test_number_rejects_non_finite_values(value: object) -> None:
    assert number_of(value) is None


def test_number_still_reads_plain_values() -> None:
    assert (number_of(3), number_of("2.5"), number_of(None)) == (3.0, 2.5, None)
    assert not math.isnan(number_of("1") or 0)


def test_zero_length_usage_window_does_not_overwrite_the_weekly_one() -> None:
    payload = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 50,
                "limit_window_seconds": 604800,
                "reset_at": 5000,
            },
            "secondary_window": {
                "used_percent": 0,
                "limit_window_seconds": 0,
                "reset_at": 0,
            },
        }
    }

    assert limits_from_usage_payload(payload, NOW) == Limits(None, Window(50, 5000))


def test_secret_straddling_the_log_cut_is_masked_not_truncated() -> None:
    body = "a" * (LOG_BODY_LIMIT_BYTES - 5) + " person@example.com tail"

    logged = json.loads(redact_for_log("main", 500, {}, body))["body"]

    assert (
        "@" not in logged
        and "person" not in logged
        and len(logged.encode()) <= LOG_BODY_LIMIT_BYTES
    )


def test_jwt_straddling_the_log_cut_is_masked() -> None:
    jwt_like = ".".join(["eyJ" + "a" * 40, "b" * 40, "c" * 40])
    body = "x" * (LOG_BODY_LIMIT_BYTES - 20) + jwt_like

    logged = json.loads(redact_for_log("main", 500, {}, body))["body"]

    assert "eyJ" not in logged and "aaaa" not in logged


# observer


def test_failing_sink_does_not_change_the_provider_error() -> None:
    def broken(failure):  # type: ignore[no-untyped-def]
        raise RuntimeError("sink down")

    install_error_observer(
        ChatGPTResponsesAPIConfig, lambda s, h, b: Unclassified(False, False), broken
    )
    try:
        with attempt_scope(AttemptContext("r", 1, "s", "d", 0)):
            with pytest.raises(BaseLLMException) as raised:
                ChatGPTResponsesAPIConfig().get_error_class("boom", 503, {})
    finally:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)

    assert raised.value.status_code == 503


def test_attempt_scope_resets_the_context() -> None:
    with attempt_scope(AttemptContext("r", 1, "s", "d", 0)):
        inside = current_attempt.get()

    assert (inside is not None, current_attempt.get()) == (True, None)


# startup


async def test_ready_flag_is_set_after_the_handlers_finished() -> None:
    host, state, seen = FakeHost(), GatewayState(), []

    async def handler() -> None:
        await asyncio.sleep(0.01)
        seen.append(state.ready.is_set())

    start_gateway(
        host,
        lambda: __import__("fastapi").APIRouter(),
        [Plugin(on_ready=(handler,))],
        state,
    )
    await state.ready_task  # type: ignore[misc]

    assert (seen, state.ready.is_set()) == ([False], True)


def test_existing_callback_is_not_instantiated_again() -> None:
    created: list[int] = []

    class Counted(Marker):
        def __init__(self) -> None:
            created.append(1)

    registered: list[object] = [Counted()]
    created.clear()

    register_callbacks(registered, [Counted])

    assert (created, len(registered)) == ([], 1)


async def test_failure_of_a_ready_handler_is_logged_and_keeps_the_gateway_closed(caplog) -> None:  # type: ignore[no-untyped-def]
    host, state = FakeHost(), GatewayState()

    async def broken() -> None:
        raise RuntimeError("state load failed")

    start_gateway(
        host,
        lambda: __import__("fastapi").APIRouter(),
        [Plugin(on_ready=(broken,))],
        state,
    )
    with pytest.raises(RuntimeError):
        await state.ready_task  # type: ignore[misc]

    assert not state.ready.is_set()


# API role and config (T)


@pytest.mark.parametrize(
    "role",
    [LitellmUserRoles.INTERNAL_USER, LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY, None],
)
async def test_non_admin_roles_are_rejected(role) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(HTTPException) as raised:
        await require_proxy_admin(SimpleNamespace(user_role=role))  # type: ignore[arg-type]

    assert raised.value.status_code == 403


async def test_proxy_admin_passes() -> None:
    auth = SimpleNamespace(user_role=LitellmUserRoles.PROXY_ADMIN)

    assert await require_proxy_admin(auth) is auth  # type: ignore[arg-type]


def test_provider_overrides_replace_only_the_given_fields() -> None:
    config = load_config(
        json.dumps(
            {
                "providers": {
                    "chatgpt": {"soft_threshold_percent": 80, "concurrency_limit": 4}
                }
            }
        )
    )

    tuning = config.tuning_for("chatgpt")

    assert (
        tuning.soft_threshold_percent,
        tuning.concurrency_limit,
        tuning.series_threshold,
    ) == (80, 4, 3)
    assert config.tuning_for("other").soft_threshold_percent == 95


def test_unknown_override_key_fails_the_load() -> None:
    with pytest.raises(ValueError):
        load_config(json.dumps({"providers": {"chatgpt": {"soft_treshold": 80}}}))


def test_missing_config_gives_defaults() -> None:
    assert load_config(None) == GatewayConfig() and load_config("") == GatewayConfig()
