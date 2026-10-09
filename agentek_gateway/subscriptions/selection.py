from collections.abc import Mapping
from dataclasses import dataclass, field
from math import inf

from .model import (
    StateRecord,
    Subscription,
    SubscriptionId,
    SubscriptionState,
    UsageRecord,
    effective_state,
    is_working,
)
from .policy import KeySubjects, Policy, eligible_tiers

CREDENTIAL_PARAM = "litellm_credential_name"
DEPLOYMENT_ID_PREFIX = "sub:"
STATE_CLASS_RANK = {SubscriptionState.ACTIVE: 0, SubscriptionState.SOFT_LIMITED: 1}


@dataclass(frozen=True, slots=True)
class Snapshot:
    subscriptions: Mapping[SubscriptionId, Subscription]
    states: Mapping[SubscriptionId, StateRecord]
    usage: Mapping[SubscriptionId, UsageRecord]
    in_flight: Mapping[SubscriptionId, int]
    policy: Policy
    unsupported: frozenset[tuple[SubscriptionId, str]]
    subscription_models: frozenset[str]
    closed: bool = False
    by_credential: Mapping[str, SubscriptionId] = field(init=False)

    def __post_init__(self) -> None:
        index = {sub.credential_name: sub.id for sub in self.subscriptions.values()}
        object.__setattr__(self, "by_credential", index)


@dataclass(frozen=True, slots=True)
class Candidate:
    deployment_id: str
    subscription_id: SubscriptionId | None = None


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    model: str
    subjects: KeySubjects | None = None
    sticky_subscription_id: SubscriptionId | None = None
    excluded_deployment_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Kept:
    candidates: tuple[Candidate, ...]
    chosen: Candidate | None
    alternatives: int
    sticky_hit: bool


@dataclass(frozen=True, slots=True)
class Exhausted:
    recovery_at: float | None


SelectionOutcome = Kept | Exhausted


def candidate_of(deployment: Mapping[str, object], snapshot: Snapshot) -> Candidate:
    model_info = deployment.get("model_info")
    params = deployment.get("litellm_params")
    deployment_id = (
        str(model_info.get("id"))
        if isinstance(model_info, Mapping) and model_info.get("id")
        else ""
    )
    subscription_id = subscription_of(deployment_id, params, snapshot)
    return Candidate(deployment_id, subscription_id)


def select(
    request: SelectionRequest,
    candidates: tuple[Candidate, ...],
    snapshot: Snapshot,
    now: float,
) -> SelectionOutcome:
    shared_candidates = tuple(
        candidate for candidate in candidates if candidate.subscription_id is None
    )
    subscription_candidates = tuple(
        candidate for candidate in candidates if candidate.subscription_id is not None
    )
    if (
        not subscription_candidates
        and request.model not in snapshot.subscription_models
    ):
        return Kept(candidates, None, 0, sticky_hit=False)

    usable = tuple(
        candidate
        for candidate in _not_excluded(request, subscription_candidates)
        if not _model_unsupported(candidate, request.model, snapshot)
    )
    tiers = _working_by_tier(request, usable, snapshot, now)
    if not tiers:
        if shared_candidates:
            return Kept(shared_candidates, None, 0, sticky_hit=False)
        return Exhausted(_recovery_at(request, subscription_candidates, snapshot, now))
    working = tiers[0]
    unexcluded_shared = len(_not_excluded(request, shared_candidates))

    sticky = next(
        (
            candidate
            for candidate in working
            if candidate.subscription_id == request.sticky_subscription_id
        ),
        None,
    )
    chosen = sticky or min(
        working, key=lambda candidate: _order_key(candidate, snapshot, now)
    )
    return Kept(
        (*shared_candidates, chosen),
        chosen,
        sum(len(tier) for tier in tiers) - 1 + unexcluded_shared,
        sticky_hit=sticky is not None,
    )


def subscription_of(
    deployment_id: str, params: object, snapshot: Snapshot
) -> SubscriptionId | None:
    if deployment_id.startswith(DEPLOYMENT_ID_PREFIX):
        _, _, rest = deployment_id.partition(DEPLOYMENT_ID_PREFIX)
        return rest.split(":", 1)[0]
    if isinstance(params, Mapping):
        credential = params.get(CREDENTIAL_PARAM)
        if isinstance(credential, str):
            return snapshot.by_credential.get(credential)
    return None


def _not_excluded(
    request: SelectionRequest, candidates: tuple[Candidate, ...]
) -> tuple[Candidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if candidate.deployment_id not in request.excluded_deployment_ids
    )


def _model_unsupported(candidate: Candidate, model: str, snapshot: Snapshot) -> bool:
    return (candidate.subscription_id, model) in snapshot.unsupported


def _working_by_tier(
    request: SelectionRequest,
    usable: tuple[Candidate, ...],
    snapshot: Snapshot,
    now: float,
) -> tuple[tuple[Candidate, ...], ...]:
    """Non-empty groups of working candidates, the tier the request is served from first."""
    present = frozenset(
        candidate.subscription_id for candidate in usable if candidate.subscription_id
    )
    own, shared = eligible_tiers(
        snapshot.policy, frozenset(snapshot.subscriptions), request.subjects
    )
    groups = []
    for tier in (own & present, shared & present):
        working = tuple(
            candidate
            for candidate in usable
            if candidate.subscription_id in tier
            and _is_working(candidate.subscription_id, snapshot, now)
        )
        if working:
            groups.append(working)
    return tuple(groups)


def _is_working(
    subscription_id: SubscriptionId | None, snapshot: Snapshot, now: float
) -> bool:
    if subscription_id is None:
        return False
    subscription = snapshot.subscriptions.get(subscription_id)
    if snapshot.closed or subscription is None or not subscription.enabled:
        return False
    record = snapshot.states.get(subscription_id)
    if record is None:
        return True
    return is_working(effective_state(record, now))


def _order_key(
    candidate: Candidate, snapshot: Snapshot, now: float
) -> tuple[int, int, float, float, str]:
    subscription_id = candidate.subscription_id or ""
    subscription = snapshot.subscriptions[subscription_id]
    record = snapshot.states.get(subscription_id)
    state = effective_state(record, now) if record else SubscriptionState.ACTIVE
    usage = snapshot.usage.get(subscription_id)
    weekly = usage.limits.weekly if usage else None
    return (
        STATE_CLASS_RANK[state],
        subscription.priority,
        weekly.reset_at if weekly else inf,
        _load(subscription, snapshot),
        subscription_id,
    )


def _load(subscription: Subscription, snapshot: Snapshot) -> float:
    in_flight = snapshot.in_flight.get(subscription.id, 0)
    if subscription.concurrency_limit:
        return in_flight / subscription.concurrency_limit
    return float(in_flight)


def _recovery_at(
    request: SelectionRequest,
    candidates: tuple[Candidate, ...],
    snapshot: Snapshot,
    now: float,
) -> float | None:
    present = frozenset(
        candidate.subscription_id
        for candidate in candidates
        if candidate.subscription_id
    )
    own, shared = eligible_tiers(
        snapshot.policy, present or frozenset(snapshot.subscriptions), request.subjects
    )
    deadlines = []
    for subscription_id in own | shared:
        record = snapshot.states.get(subscription_id)
        subscription = snapshot.subscriptions.get(subscription_id)
        if record is None or subscription is None or not subscription.enabled:
            continue
        if is_working(effective_state(record, now)):
            continue
        if record.until is not None and record.until > now:
            deadlines.append(record.until)
    return min(deadlines) if deadlines else None
