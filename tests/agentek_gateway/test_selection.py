import random

import pytest

from agentek_gateway.subscriptions.model import SubscriptionState as S
from agentek_gateway.subscriptions.policy import (
    KeySubjects,
    Policy,
    Visibility,
    VisibilityKind,
    key_subjects_from_metadata,
)
from agentek_gateway.subscriptions.selection import (
    Candidate,
    Exhausted,
    Kept,
    SelectionRequest,
    candidate_of,
    select,
)

from .builders import (
    MODEL,
    NOW,
    deployments_for,
    snapshot_of,
    state_record,
    subject,
    weekly_reset,
)
from .conftest import make_subscription

HOUR = 3600.0


def chosen_id(outcome) -> str | None:  # type: ignore[no-untyped-def]
    assert isinstance(outcome, Kept)
    return outcome.chosen.subscription_id if outcome.chosen else None


def pick(request: SelectionRequest, candidates, snapshot):  # type: ignore[no-untyped-def]
    return select(request, candidates, snapshot, NOW)


def plain(**kwargs: object) -> SelectionRequest:
    return SelectionRequest(model=MODEL, **kwargs)  # type: ignore[arg-type]


def test_active_beats_soft_limited_even_with_worse_priority() -> None:
    subs = [make_subscription("a", priority=10), make_subscription("b", priority=90)]
    snapshot = snapshot_of(subs, states={"a": state_record(S.SOFT_LIMITED, NOW + HOUR)})

    assert chosen_id(pick(plain(), deployments_for("a", "b"), snapshot)) == "b"


def test_lower_priority_number_goes_first() -> None:
    subs = [
        make_subscription("a", priority=60),
        make_subscription("b", priority=20),
        make_subscription("c"),
    ]

    assert (
        chosen_id(pick(plain(), deployments_for("a", "b", "c"), snapshot_of(subs)))
        == "b"
    )


def test_window_that_resets_sooner_goes_first() -> None:
    subs = [make_subscription("a"), make_subscription("b")]
    usage = {"a": weekly_reset(NOW + 5 * 86400), "b": weekly_reset(NOW + 86400)}

    assert (
        chosen_id(
            pick(plain(), deployments_for("a", "b"), snapshot_of(subs, usage=usage))
        )
        == "b"
    )


def test_unknown_window_goes_after_known() -> None:
    subs = [make_subscription("a"), make_subscription("b")]
    usage = {"b": weekly_reset(NOW + 9 * 86400)}

    assert (
        chosen_id(
            pick(plain(), deployments_for("a", "b"), snapshot_of(subs, usage=usage))
        )
        == "b"
    )


def test_lower_load_ratio_goes_first() -> None:
    subs = [
        make_subscription("a", concurrency_limit=10),
        make_subscription("b", concurrency_limit=2),
    ]
    snapshot = snapshot_of(subs, in_flight={"a": 3, "b": 1})

    assert chosen_id(pick(plain(), deployments_for("a", "b"), snapshot)) == "a"


def test_unlimited_subscriptions_compare_by_in_flight_count() -> None:
    subs = [make_subscription("a"), make_subscription("b")]

    assert (
        chosen_id(
            pick(
                plain(),
                deployments_for("a", "b"),
                snapshot_of(subs, in_flight={"a": 4, "b": 2}),
            )
        )
        == "b"
    )


def test_smaller_id_breaks_the_tie() -> None:
    subs = [make_subscription("b"), make_subscription("a")]

    assert chosen_id(pick(plain(), deployments_for("b", "a"), snapshot_of(subs))) == "a"


@pytest.mark.parametrize(
    "state",
    [
        S.RATE_LIMITED,
        S.HALF_OPEN,
        S.OVERLOADED,
        S.BROKEN,
        S.AUTH_REFRESHING,
        S.AUTH_FAILED,
        S.BANNED,
        S.DISABLED,
    ],
)
def test_non_working_states_are_never_chosen(state: S) -> None:
    subs = [make_subscription("a", priority=1), make_subscription("b")]
    snapshot = snapshot_of(subs, states={"a": state_record(state, NOW + HOUR)})

    assert chosen_id(pick(plain(), deployments_for("a", "b"), snapshot)) == "b"


def test_elapsed_soft_limit_counts_as_active_again() -> None:
    subs = [make_subscription("a", priority=10), make_subscription("b", priority=90)]
    snapshot = snapshot_of(subs, states={"a": state_record(S.SOFT_LIMITED, NOW - 1)})

    assert chosen_id(pick(plain(), deployments_for("a", "b"), snapshot)) == "a"


def test_elapsed_rate_limit_waits_for_the_probe() -> None:
    subs = [make_subscription("a", priority=10), make_subscription("b", priority=90)]
    snapshot = snapshot_of(subs, states={"a": state_record(S.RATE_LIMITED, NOW - 1)})

    assert chosen_id(pick(plain(), deployments_for("a", "b"), snapshot)) == "b"


def test_disabled_flag_removes_the_subscription() -> None:
    subs = [make_subscription("a", enabled=False), make_subscription("b")]

    assert chosen_id(pick(plain(), deployments_for("a", "b"), snapshot_of(subs))) == "b"


def test_failed_deployment_of_this_request_is_not_chosen_again() -> None:
    subs = [make_subscription("a"), make_subscription("b")]
    request = plain(excluded_deployment_ids=frozenset({f"sub:a:{MODEL}"}))

    assert chosen_id(pick(request, deployments_for("a", "b"), snapshot_of(subs))) == "b"


def test_model_rejected_by_the_account_is_skipped_only_for_that_model() -> None:
    subs = [make_subscription("a", priority=1), make_subscription("b")]
    snapshot = snapshot_of(subs, unsupported=frozenset({("a", MODEL)}))

    assert (
        chosen_id(pick(plain(), deployments_for("a", "b"), snapshot)),
        chosen_id(
            select(SelectionRequest("other"), deployments_for("a", "b"), snapshot, NOW)
        ),
    ) == ("b", "a")


def test_deployment_of_a_deleted_subscription_is_not_served() -> None:
    subs = [make_subscription("a")]
    candidates = (*deployments_for("a"), Candidate(f"sub:gone:{MODEL}", "gone"))

    outcome = pick(
        plain(excluded_deployment_ids=frozenset({f"sub:a:{MODEL}"})),
        candidates,
        snapshot_of(subs),
    )

    assert isinstance(outcome, Exhausted)


def test_result_holds_exactly_one_subscription_deployment() -> None:
    subs = [make_subscription(name) for name in "abcd"]

    outcome = pick(plain(), deployments_for(*"abcd"), snapshot_of(subs))

    assert (
        isinstance(outcome, Kept)
        and len(outcome.candidates) == 1
        and outcome.alternatives == 3
    )


def test_deployments_without_subscriptions_pass_through_with_the_chosen_one() -> None:
    subs = [make_subscription("a")]
    shared = Candidate("deepseek-1")

    outcome = pick(plain(), (shared, *deployments_for("a")), snapshot_of(subs))

    assert isinstance(outcome, Kept) and set(outcome.candidates) == {
        shared,
        *deployments_for("a"),
    }


def test_deepseek_stays_available_when_every_subscription_is_blocked() -> None:
    subs = [make_subscription("a")]
    shared = Candidate("deepseek-1")
    snapshot = snapshot_of(subs, states={"a": state_record(S.RATE_LIMITED, NOW + HOUR)})

    outcome = pick(plain(), (shared, *deployments_for("a")), snapshot)

    assert isinstance(outcome, Kept) and outcome.candidates == (shared,)


def test_group_without_subscriptions_is_returned_untouched() -> None:
    shared = (Candidate("deepseek-1"), Candidate("deepseek-2"))

    outcome = select(
        SelectionRequest("deepseek"), shared, snapshot_of([], models=frozenset()), NOW
    )

    assert outcome == Kept(shared, None, 0, sticky_hit=False)


def test_empty_candidate_list_of_a_subscription_model_is_exhausted() -> None:
    assert isinstance(
        pick(plain(), (), snapshot_of([make_subscription("a")])), Exhausted
    )


def test_empty_candidate_list_of_a_plain_model_is_not_ours_to_fail() -> None:
    outcome = select(
        SelectionRequest("deepseek"), (), snapshot_of([], models=frozenset()), NOW
    )

    assert outcome == Kept((), None, 0, sticky_hit=False)


def test_exhausted_reports_the_nearest_recovery() -> None:
    subs = [make_subscription("a"), make_subscription("b"), make_subscription("c")]
    states = {
        "a": state_record(S.RATE_LIMITED, NOW + 2 * HOUR),
        "b": state_record(S.RATE_LIMITED, NOW + HOUR),
        "c": state_record(S.BANNED),
    }

    outcome = pick(
        plain(), deployments_for("a", "b", "c"), snapshot_of(subs, states=states)
    )

    assert outcome == Exhausted(NOW + HOUR)


def test_exhausted_without_any_deadline_has_no_recovery_time() -> None:
    subs = [make_subscription("a")]
    snapshot = snapshot_of(subs, states={"a": state_record(S.AUTH_FAILED)})

    assert pick(plain(), deployments_for("a"), snapshot) == Exhausted(None)


def test_target_order_limits_the_candidates() -> None:
    subs = [make_subscription("a", priority=1), make_subscription("b")]
    candidates = (
        Candidate(f"sub:a:{MODEL}", "a", order=1),
        Candidate(f"sub:b:{MODEL}", "b", order=2),
    )

    assert chosen_id(pick(plain(target_order=2), candidates, snapshot_of(subs))) == "b"


def test_lowest_order_group_wins_without_a_target() -> None:
    subs = [make_subscription("a", priority=90), make_subscription("b", priority=1)]
    candidates = (
        Candidate(f"sub:a:{MODEL}", "a", order=1),
        Candidate(f"sub:b:{MODEL}", "b", order=2),
    )

    assert chosen_id(pick(plain(), candidates, snapshot_of(subs))) == "a"


def test_unknown_target_order_keeps_all_candidates() -> None:
    subs = [make_subscription("a"), make_subscription("b")]
    candidates = (
        Candidate(f"sub:a:{MODEL}", "a", order=1),
        Candidate(f"sub:b:{MODEL}", "b", order=2),
    )

    assert chosen_id(pick(plain(target_order=9), candidates, snapshot_of(subs))) == "a"


def test_credential_name_maps_a_console_made_deployment_to_its_subscription() -> None:
    snapshot = snapshot_of([make_subscription("a", credential_name="cred-a")])
    deployment = {
        "model_info": {"id": "dep-1"},
        "litellm_params": {"litellm_credential_name": "cred-a", "order": 3},
    }

    assert candidate_of(deployment, snapshot) == Candidate("dep-1", "a", 3)


def test_deployment_with_another_credential_is_not_a_subscription() -> None:
    deployment = {
        "model_info": {"id": "dep-1"},
        "litellm_params": {"litellm_credential_name": "openai-default"},
    }

    assert candidate_of(deployment, snapshot_of([make_subscription("a")])) == Candidate(
        "dep-1", None, None
    )


# policy

E1, E2 = subject("employee", "e1"), subject("employee", "e2")
S1, S2 = subject("space", "s1"), subject("space", "s2")


def keys(employee=None, space=None):  # type: ignore[no-untyped-def]
    return KeySubjects(employee=employee, space=space)


def policy(visibility=None, bindings=None):  # type: ignore[no-untyped-def]
    return Policy(visibility=visibility or {}, bindings=bindings or {})


def test_excluded_employee_does_not_get_the_subscription() -> None:
    subs = [make_subscription("x", priority=1), make_subscription("y")]
    rules = policy({"x": Visibility(VisibilityKind.ALL_EXCEPT, frozenset({E1}))})
    snapshot = snapshot_of(subs, policy=rules)

    assert (
        chosen_id(pick(plain(subjects=keys(E1)), deployments_for("x", "y"), snapshot)),
        chosen_id(pick(plain(subjects=keys(E2)), deployments_for("x", "y"), snapshot)),
    ) == ("y", "x")


def test_exclusion_holds_for_a_space_of_the_excluded_employee_before_labels_arrive() -> (
    None
):
    subs = [make_subscription("x", priority=1), make_subscription("y")]
    snapshot = snapshot_of(
        subs,
        policy=policy({"x": Visibility(VisibilityKind.ALL_EXCEPT, frozenset({E1}))}),
    )

    assert (
        chosen_id(pick(plain(subjects=None), deployments_for("x", "y"), snapshot))
        == "y"
    )


def test_only_visibility_admits_every_key_of_the_listed_space() -> None:
    subs = [make_subscription("x", priority=1), make_subscription("y")]
    snapshot = snapshot_of(
        subs, policy=policy({"x": Visibility(VisibilityKind.ONLY, frozenset({S1}))})
    )

    assert (
        chosen_id(
            pick(plain(subjects=keys(E2, S1)), deployments_for("x", "y"), snapshot)
        ),
        chosen_id(
            pick(plain(subjects=keys(E2, S2)), deployments_for("x", "y"), snapshot)
        ),
        chosen_id(pick(plain(subjects=None), deployments_for("x", "y"), snapshot)),
    ) == ("x", "y", "y")


def test_bound_subscription_goes_only_to_its_subject() -> None:
    subs = [make_subscription("d", priority=1), make_subscription("g")]
    snapshot = snapshot_of(subs, policy=policy(bindings={"d": frozenset({S1})}))

    assert (
        chosen_id(
            pick(plain(subjects=keys(E1, S1)), deployments_for("d", "g"), snapshot)
        ),
        chosen_id(
            pick(plain(subjects=keys(E1, S2)), deployments_for("d", "g"), snapshot)
        ),
    ) == ("d", "g")


def test_bound_subscriptions_are_exhausted_for_others_even_when_nothing_else_is_left() -> (
    None
):
    subs = [make_subscription("d")]
    snapshot = snapshot_of(subs, policy=policy(bindings={"d": frozenset({S1})}))

    assert isinstance(
        pick(plain(subjects=keys(E1, S2)), deployments_for("d"), snapshot), Exhausted
    )


def test_space_binding_beats_employee_binding() -> None:
    subs = [make_subscription("d1"), make_subscription("d2")]
    snapshot = snapshot_of(
        subs, policy=policy(bindings={"d1": frozenset({E1}), "d2": frozenset({S1})})
    )

    assert (
        chosen_id(
            pick(plain(subjects=keys(E1, S1)), deployments_for("d1", "d2"), snapshot)
        )
        == "d2"
    )


def test_employee_binding_applies_when_the_space_has_none() -> None:
    subs = [make_subscription("d1"), make_subscription("g")]
    snapshot = snapshot_of(subs, policy=policy(bindings={"d1": frozenset({E1})}))

    assert (
        chosen_id(
            pick(plain(subjects=keys(E1, S2)), deployments_for("d1", "g"), snapshot)
        )
        == "d1"
    )


def test_employee_binding_is_not_a_fallback_for_a_space_with_its_own() -> None:
    subs = [make_subscription("d1"), make_subscription("d2")]
    snapshot = snapshot_of(
        subs,
        policy=policy(bindings={"d1": frozenset({E1}), "d2": frozenset({S1})}),
        states={"d2": state_record(S.RATE_LIMITED, NOW + HOUR)},
    )

    assert isinstance(
        pick(plain(subjects=keys(E1, S1)), deployments_for("d1", "d2"), snapshot),
        Exhausted,
    )


def test_exhausted_bound_subscriptions_fall_through_to_shared() -> None:
    subs = [make_subscription("d", priority=1), make_subscription("g")]
    snapshot = snapshot_of(
        subs,
        policy=policy(bindings={"d": frozenset({S1})}),
        states={"d": state_record(S.RATE_LIMITED, NOW + HOUR)},
    )

    assert (
        chosen_id(
            pick(plain(subjects=keys(E1, S1)), deployments_for("d", "g"), snapshot)
        )
        == "g"
    )


def test_soft_limited_bound_subscription_still_beats_an_active_shared_one() -> None:
    subs = [make_subscription("d", priority=90), make_subscription("g", priority=1)]
    snapshot = snapshot_of(
        subs,
        policy=policy(bindings={"d": frozenset({S1})}),
        states={"d": state_record(S.SOFT_LIMITED, NOW + HOUR)},
    )

    assert (
        chosen_id(
            pick(plain(subjects=keys(E1, S1)), deployments_for("d", "g"), snapshot)
        )
        == "d"
    )


def test_bound_subscription_failing_in_this_request_hands_over_to_shared() -> None:
    subs = [make_subscription("d", priority=1), make_subscription("g")]
    snapshot = snapshot_of(subs, policy=policy(bindings={"d": frozenset({S1})}))
    request = plain(
        subjects=keys(E1, S1), excluded_deployment_ids=frozenset({f"sub:d:{MODEL}"})
    )

    assert chosen_id(pick(request, deployments_for("d", "g"), snapshot)) == "g"


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {"agentek_subjects": {"employee": "e1", "space": 7}},
            KeySubjects(employee=E1, space=subject("space", "7")),
        ),
        (
            {"agentek_subjects": {"service": "kb"}},
            KeySubjects(service=subject("service", "kb")),
        ),
        ({"agentek_subjects": {}}, None),
        ({"agentek_subjects": {"employee": ""}}, None),
        ({"agentek_subjects": "e1"}, None),
        ({"other": 1}, None),
        (None, None),
        ("e1", None),
    ],
)
def test_subjects_come_only_from_the_key_metadata_labels(metadata, expected) -> None:  # type: ignore[no-untyped-def]
    assert key_subjects_from_metadata(metadata) == expected


# stickiness


def test_chat_stays_on_its_subscription_while_it_is_valid() -> None:
    subs = [make_subscription("a", priority=1), make_subscription("b", priority=90)]

    outcome = pick(
        plain(sticky_subscription_id="b"), deployments_for("a", "b"), snapshot_of(subs)
    )

    assert isinstance(outcome, Kept) and (chosen_id(outcome), outcome.sticky_hit) == (
        "b",
        True,
    )


def test_chat_leaves_a_blocked_subscription() -> None:
    subs = [make_subscription("a", priority=1), make_subscription("b", priority=90)]
    snapshot = snapshot_of(subs, states={"b": state_record(S.RATE_LIMITED, NOW + HOUR)})

    outcome = pick(
        plain(sticky_subscription_id="b"), deployments_for("a", "b"), snapshot
    )

    assert isinstance(outcome, Kept) and (chosen_id(outcome), outcome.sticky_hit) == (
        "a",
        False,
    )


def test_chat_leaves_a_subscription_closed_to_its_space() -> None:
    subs = [make_subscription("a", priority=1), make_subscription("b", priority=90)]
    snapshot = snapshot_of(
        subs,
        policy=policy({"b": Visibility(VisibilityKind.ALL_EXCEPT, frozenset({S1}))}),
    )

    outcome = pick(
        plain(sticky_subscription_id="b", subjects=keys(E1, S1)),
        deployments_for("a", "b"),
        snapshot,
    )

    assert chosen_id(outcome) == "a"


def test_chat_does_not_return_to_a_subscription_that_failed_in_this_request() -> None:
    subs = [make_subscription("a", priority=1), make_subscription("b", priority=90)]
    request = plain(
        sticky_subscription_id="b",
        excluded_deployment_ids=frozenset({f"sub:b:{MODEL}"}),
    )

    assert chosen_id(pick(request, deployments_for("a", "b"), snapshot_of(subs))) == "a"


def test_chat_moves_to_a_newly_bound_subscription() -> None:
    subs = [make_subscription("g", priority=1), make_subscription("d", priority=90)]
    snapshot = snapshot_of(subs, policy=policy(bindings={"d": frozenset({S1})}))

    assert (
        chosen_id(
            pick(
                plain(sticky_subscription_id="g", subjects=keys(E1, S1)),
                deployments_for("g", "d"),
                snapshot,
            )
        )
        == "d"
    )


def test_sticky_subscription_without_a_deployment_for_the_model_is_ignored() -> None:
    subs = [make_subscription("a"), make_subscription("b")]

    assert (
        chosen_id(
            pick(
                plain(sticky_subscription_id="b"),
                deployments_for("a"),
                snapshot_of(subs),
            )
        )
        == "a"
    )


# properties

CASES = 400


def random_world(rng: random.Random):  # type: ignore[no-untyped-def]
    count = rng.randint(1, 7)
    ids = [f"s{index}" for index in range(count)]
    subs = [
        make_subscription(
            sub_id,
            priority=rng.choice([10, 50, 50, 90]),
            concurrency_limit=rng.choice([None, 2, 8]),
            enabled=rng.random() > 0.1,
        )
        for sub_id in ids
    ]
    states = {
        sub_id: state_record(
            rng.choice(list(S)), NOW + rng.choice([-10.0, 10.0, 1000.0])
        )
        for sub_id in ids
        if rng.random() < 0.6
    }
    usage = {
        sub_id: weekly_reset(NOW + rng.randint(1, 9) * 3600)
        for sub_id in ids
        if rng.random() < 0.5
    }
    in_flight = {sub_id: rng.randint(0, 5) for sub_id in ids}
    people = [E1, E2, S1, S2]
    visibility = {
        sub_id: Visibility(
            rng.choice(list(VisibilityKind)),
            frozenset(rng.sample(people, rng.randint(1, 2))),
        )
        for sub_id in ids
        if rng.random() < 0.4
    }
    bindings = {
        sub_id: frozenset({rng.choice(people)}) for sub_id in ids if rng.random() < 0.3
    }
    unsupported = frozenset((sub_id, MODEL) for sub_id in ids if rng.random() < 0.2)
    snapshot = snapshot_of(
        subs,
        states=states,
        usage=usage,
        in_flight=in_flight,
        policy=Policy(visibility=visibility, bindings=bindings),
        unsupported=unsupported,
    )
    excluded = frozenset(
        f"sub:{sub_id}:{MODEL}" for sub_id in ids if rng.random() < 0.2
    )
    subjects = rng.choice(
        [
            None,
            keys(E1),
            keys(E2, S1),
            keys(E1, S2),
            KeySubjects(service=subject("service", "k")),
        ]
    )
    sticky = rng.choice([None, *ids])
    return snapshot, ids, SelectionRequest(MODEL, subjects, sticky, excluded), subjects


def admissible(snapshot, request) -> set[str]:  # type: ignore[no-untyped-def]
    from agentek_gateway.subscriptions.model import effective_state, is_working

    result = set()
    for sub_id, sub in snapshot.subscriptions.items():
        record = snapshot.states.get(sub_id)
        working = sub.enabled and (
            record is None or is_working(effective_state(record, NOW))
        )
        usable = (
            f"sub:{sub_id}:{MODEL}" not in request.excluded_deployment_ids
            and (sub_id, MODEL) not in snapshot.unsupported
        )
        if working and usable:
            result.add(sub_id)
    return result


def test_property_chosen_subscription_is_admissible_and_unique() -> None:
    rng = random.Random(20261009)
    for _ in range(CASES):
        snapshot, ids, request, _ = random_world(rng)

        outcome = select(request, deployments_for(*ids), snapshot, NOW)

        if isinstance(outcome, Exhausted):
            continue
        assert len(outcome.candidates) == 1
        assert (
            outcome.chosen is not None
            and outcome.chosen.subscription_id in admissible(snapshot, request)
        )


def test_property_exhaustion_means_no_admissible_subscription_is_eligible() -> None:
    from agentek_gateway.subscriptions.policy import eligible_tiers

    rng = random.Random(7)
    for _ in range(CASES):
        snapshot, ids, request, subjects = random_world(rng)

        outcome = select(request, deployments_for(*ids), snapshot, NOW)

        own, shared = eligible_tiers(snapshot.policy, frozenset(ids), subjects)
        reachable = admissible(snapshot, request) & (
            (own if own & admissible(snapshot, request) else shared) | own
        )
        assert isinstance(outcome, Exhausted) == (not reachable)


def test_property_choice_does_not_depend_on_candidate_order() -> None:
    rng = random.Random(11)
    for _ in range(CASES):
        snapshot, ids, request, _ = random_world(rng)
        shuffled = list(ids)
        rng.shuffle(shuffled)

        first = select(request, deployments_for(*ids), snapshot, NOW)
        second = select(request, deployments_for(*shuffled), snapshot, NOW)

        assert first == second or (
            isinstance(first, Kept)
            and isinstance(second, Kept)
            and first.chosen == second.chosen
        )


def test_property_without_stickiness_the_best_by_the_documented_order_wins() -> None:
    rng = random.Random(13)
    for _ in range(CASES):
        snapshot, ids, request, _ = random_world(rng)
        request = SelectionRequest(
            MODEL, request.subjects, None, request.excluded_deployment_ids
        )

        outcome = select(request, deployments_for(*ids), snapshot, NOW)

        if isinstance(outcome, Exhausted):
            continue
        rivals = [rival for rival in outcome_rivals(snapshot, request, ids) if rival != outcome.chosen.subscription_id]  # type: ignore[union-attr]
        assert all(order_tuple(snapshot, outcome.chosen.subscription_id) <= order_tuple(snapshot, rival) for rival in rivals)  # type: ignore[union-attr]


def outcome_rivals(snapshot, request, ids):  # type: ignore[no-untyped-def]
    from agentek_gateway.subscriptions.policy import eligible_tiers

    own, shared = eligible_tiers(snapshot.policy, frozenset(ids), request.subjects)
    ready = admissible(snapshot, request)
    return sorted((own if own & ready else shared) & ready)


def order_tuple(snapshot, sub_id):  # type: ignore[no-untyped-def]
    from math import inf

    sub = snapshot.subscriptions[sub_id]
    record = snapshot.states.get(sub_id)
    soft = (
        1
        if record is not None and record.state is S.SOFT_LIMITED and record.until > NOW
        else 0
    )
    usage = snapshot.usage.get(sub_id)
    weekly = usage.limits.weekly.reset_at if usage else inf
    in_flight = snapshot.in_flight.get(sub_id, 0)
    load = (
        in_flight / sub.concurrency_limit if sub.concurrency_limit else float(in_flight)
    )
    return (soft, sub.priority, weekly, load, sub_id)


def test_property_valid_sticky_subscription_is_kept() -> None:
    rng = random.Random(17)
    for _ in range(CASES):
        snapshot, ids, request, _ = random_world(rng)
        sticky = request.sticky_subscription_id
        if sticky is None:
            continue
        reachable = set(outcome_rivals(snapshot, request, ids))

        outcome = select(request, deployments_for(*ids), snapshot, NOW)

        if sticky in reachable:
            assert isinstance(outcome, Kept) and outcome.chosen.subscription_id == sticky  # type: ignore[union-attr]
