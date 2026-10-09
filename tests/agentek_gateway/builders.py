from typing import Mapping

from agentek_gateway.subscriptions.model import (
    Limits,
    SignalSource,
    StateReason,
    StateRecord,
    Subscription,
    SubscriptionState,
    UsageRecord,
    Window,
)
from agentek_gateway.subscriptions.policy import Policy, Subject
from agentek_gateway.subscriptions.selection import Candidate, Snapshot

NOW = 1_000_000.0
MODEL = "gpt-x"


def subject(kind: str, ident: str) -> Subject:
    from agentek_gateway.subscriptions.policy import SubjectKind

    return Subject(SubjectKind(kind), ident)


def state_record(
    state: SubscriptionState,
    until: float | None = None,
    streak: int = 0,
    version: int = 1,
) -> StateRecord:
    return StateRecord(
        state, version, NOW - 5, until, StateReason.NONE, SignalSource.NONE, streak
    )


def snapshot_of(
    subscriptions: list[Subscription],
    *,
    states: Mapping[str, StateRecord] | None = None,
    usage: Mapping[str, UsageRecord] | None = None,
    in_flight: Mapping[str, int] | None = None,
    policy: Policy | None = None,
    unsupported: frozenset[tuple[str, str]] = frozenset(),
    models: frozenset[str] = frozenset({MODEL}),
) -> Snapshot:
    return Snapshot(
        subscriptions={sub.id: sub for sub in subscriptions},
        states=states or {},
        usage=usage or {},
        in_flight=in_flight or {},
        policy=policy or Policy(),
        unsupported=unsupported,
        subscription_models=models,
    )


def weekly_reset(at: float) -> UsageRecord:
    return UsageRecord(Limits(weekly=Window(10.0, at)), NOW)


def deployments_for(*sub_ids: str, order: int | None = None) -> tuple[Candidate, ...]:
    return tuple(
        Candidate(f"sub:{sub_id}:{MODEL}", sub_id, order) for sub_id in sub_ids
    )
