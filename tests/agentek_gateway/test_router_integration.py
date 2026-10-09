import litellm
import pytest
from litellm import Router
from litellm.integrations.custom_logger import CustomLogger

from agentek_gateway.subscriptions.attempts import REQUEST_ID_FIELD, AttemptTracker
from agentek_gateway.subscriptions.errors import NoAvailableSubscriptionsError
from agentek_gateway.subscriptions.filtering import FilterContext, filter_deployments
from agentek_gateway.subscriptions.model import SubscriptionState as S

from .builders import snapshot_of, state_record
from .conftest import FakeClock, make_subscription

MODEL = "sub-model"
RETRY_AFTER = 10


class FilterProbe(CustomLogger):
    """Minimal stand-in for the B2 adapter: calls the pure filter exactly as the adapter will."""

    def __init__(self, snapshot, clock: FakeClock) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.snapshot = snapshot
        self.clock = clock
        self.attempts = AttemptTracker(clock, 900.0)
        self.calls = 0
        self.picked: list[str] = []

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        kwargs = request_kwargs or {}
        request_id = (kwargs.get("metadata") or {}).get(REQUEST_ID_FIELD) or "req"
        result = filter_deployments(
            model,
            healthy_deployments,
            self.snapshot,
            FilterContext(
                request_kwargs=kwargs,
                attempted=self.attempts.attempted(request_id),
                sticky_subscription_id=None,
                now=self.clock.now(),
                retry_after_s=RETRY_AFTER,
            ),
        )
        if result.chosen:
            self.attempts.record(
                request_id, result.chosen.deployment_id, result.alternatives
            )
            self.picked.append(result.chosen.subscription_id or "")
        return result.deployments


def build_router(sub_ids: list[str], *, failing: set[str], states=None):  # type: ignore[no-untyped-def]
    subs = [make_subscription(sub_id) for sub_id in sub_ids]
    model_list = [
        {
            "model_name": MODEL,
            "litellm_params": {
                "model": "openai/fake",
                "api_key": "k",
                "mock_response": (
                    "litellm.RateLimitError" if sub_id in failing else "fine"
                ),
            },
            "model_info": {"id": f"sub:{sub_id}:{MODEL}"},
        }
        for sub_id in sub_ids
    ]
    clock = FakeClock()
    probe = FilterProbe(
        snapshot_of(subs, states=states or {}, models=frozenset({MODEL})), clock
    )
    router = Router(model_list=model_list, num_retries=4, retry_after=0)
    return router, probe


@pytest.fixture(autouse=True)
def clean_callbacks():  # type: ignore[no-untyped-def]
    saved = list(litellm.callbacks)
    yield
    litellm.callbacks = saved


async def test_router_calls_the_filter_and_gets_the_best_subscription() -> None:
    router, probe = build_router(["a", "b"], failing=set())
    litellm.callbacks = [probe]

    response = await router.acompletion(
        model=MODEL, messages=[{"role": "user", "content": "x"}]
    )

    assert (response.choices[0].message.content, probe.picked) == ("fine", ["a"])


async def test_every_subscription_blocked_gives_429_with_retry_after_in_one_filter_call() -> (
    None
):
    states = {
        sub_id: state_record(S.RATE_LIMITED, 1_000_000.0 + 7200)
        for sub_id in ("a", "b")
    }
    router, probe = build_router(["a", "b"], failing=set(), states=states)
    litellm.callbacks = [probe]

    with pytest.raises(NoAvailableSubscriptionsError) as raised:
        await router.acompletion(
            model=MODEL, messages=[{"role": "user", "content": "x"}]
        )

    assert (
        raised.value.status_code,
        raised.value.headers,
        probe.calls,
        "2 h" in raised.value.message,
    ) == (
        429,
        {"retry-after": "10"},
        1,
        True,
    )


async def test_single_subscription_group_ends_without_router_backoff() -> None:
    states = {"a": state_record(S.BANNED)}
    router, probe = build_router(["a"], failing=set(), states=states)
    litellm.callbacks = [probe]

    with pytest.raises(NoAvailableSubscriptionsError):
        await router.acompletion(
            model=MODEL, messages=[{"role": "user", "content": "x"}]
        )

    assert probe.calls == 1


async def test_failed_attempt_moves_to_the_next_subscription_not_back() -> None:
    router, probe = build_router(["a", "b"], failing={"a"})
    litellm.callbacks = [probe]

    response = await router.acompletion(
        model=MODEL,
        messages=[{"role": "user", "content": "x"}],
        metadata={REQUEST_ID_FIELD: "r1"},
    )

    assert (response.choices[0].message.content, probe.picked) == ("fine", ["a", "b"])
