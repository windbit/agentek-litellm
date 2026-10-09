from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .attempts import request_metadata
from .errors import NoAvailableSubscriptionsError
from .policy import key_subjects_from_metadata
from .selection import (
    Candidate,
    Exhausted,
    Kept,
    SelectionRequest,
    Snapshot,
    candidate_of,
    select,
)

Deployment = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FilterResult:
    deployments: list[Deployment]
    chosen: Candidate | None
    alternatives: int
    sticky_hit: bool


@dataclass(frozen=True, slots=True)
class FilterContext:
    request_kwargs: Mapping[str, object]
    attempted: frozenset[str]
    sticky_subscription_id: str | None
    now: float
    retry_after_s: int


def filter_deployments(
    model: str,
    deployments: Sequence[Deployment],
    snapshot: Snapshot,
    context: FilterContext,
) -> FilterResult:
    """Keeps non-subscription deployments and at most one subscription deployment; raises when none can serve."""
    pairs = [
        (candidate_of(deployment, snapshot), deployment) for deployment in deployments
    ]
    candidates = tuple(candidate for candidate, _ in pairs)
    request = SelectionRequest(
        model=model,
        subjects=key_subjects_from_metadata(
            request_metadata(context.request_kwargs).get("user_api_key_metadata")
        ),
        sticky_subscription_id=context.sticky_subscription_id,
        excluded_deployment_ids=context.attempted
        | _router_excluded(context.request_kwargs),
        target_order=_target_order(context.request_kwargs),
    )
    outcome = select(request, candidates, snapshot, context.now)
    match outcome:
        case Exhausted(recovery_at=recovery_at):
            recovery_in = (
                None if recovery_at is None else max(0.0, recovery_at - context.now)
            )
            raise NoAvailableSubscriptionsError(
                model, recovery_in, context.retry_after_s
            )
        case Kept(
            candidates=kept,
            chosen=chosen,
            alternatives=alternatives,
            sticky_hit=sticky_hit,
        ):
            return FilterResult(
                [deployment for candidate, deployment in pairs if candidate in kept],
                chosen,
                alternatives,
                sticky_hit,
            )


def _router_excluded(request_kwargs: Mapping[str, object]) -> frozenset[str]:
    value = request_kwargs.get("_excluded_deployment_ids")
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in value)
    return frozenset()


def _target_order(request_kwargs: Mapping[str, object]) -> int | None:
    value = request_kwargs.get("_target_order")
    return value if isinstance(value, int) and not isinstance(value, bool) else None
