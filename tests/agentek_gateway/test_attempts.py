from agentek_gateway.subscriptions.attempts import (
    REQUEST_ID_FIELD,
    AttemptTracker,
    issue_request_id,
    read_request_id,
    request_metadata,
)

from .conftest import FakeClock

TTL = 900.0


def tracker(clock: FakeClock, max_requests: int = 1000) -> AttemptTracker:
    return AttemptTracker(clock, TTL, max_requests)


def test_attempts_accumulate_within_a_request(clock: FakeClock) -> None:
    attempts = tracker(clock)
    attempts.record("r1", "dep-a", alternatives=2)
    attempts.record("r1", "dep-b", alternatives=1)

    assert (attempts.attempted("r1"), attempts.alternatives("r1")) == (
        frozenset({"dep-a", "dep-b"}),
        1,
    )


def test_requests_do_not_share_attempts(clock: FakeClock) -> None:
    attempts = tracker(clock)
    attempts.record("r1", "dep-a", alternatives=0)

    assert attempts.attempted("r2") == frozenset()


def test_unknown_or_missing_request_id_has_no_attempts(clock: FakeClock) -> None:
    assert (tracker(clock).attempted(None), tracker(clock).alternatives("x")) == (
        frozenset(),
        0,
    )


def test_finish_forgets_the_request(clock: FakeClock) -> None:
    attempts = tracker(clock)
    attempts.record("r1", "dep-a", alternatives=0)

    attempts.finish("r1")

    assert (attempts.attempted("r1"), attempts.size()) == (frozenset(), 0)


def test_finishing_an_unknown_request_is_harmless(clock: FakeClock) -> None:
    attempts = tracker(clock)

    attempts.finish("nope")
    attempts.finish(None)

    assert attempts.size() == 0


def test_abandoned_request_expires_after_the_ttl(clock: FakeClock) -> None:
    attempts = tracker(clock)
    attempts.record("r1", "dep-a", alternatives=0)
    clock.advance(TTL - 1)
    still_there = attempts.attempted("r1")
    clock.advance(1)

    assert (still_there, attempts.attempted("r1"), attempts.size()) == (
        frozenset({"dep-a"}),
        frozenset(),
        0,
    )


def test_each_new_attempt_extends_the_ttl(clock: FakeClock) -> None:
    attempts = tracker(clock)
    attempts.record("r1", "dep-a", alternatives=1)
    clock.advance(TTL - 1)
    attempts.record("r1", "dep-b", alternatives=0)
    clock.advance(TTL - 1)

    assert attempts.attempted("r1") == frozenset({"dep-a", "dep-b"})


def test_expired_requests_are_swept_when_new_ones_arrive(clock: FakeClock) -> None:
    attempts = tracker(clock)
    for index in range(10):
        attempts.record(f"old-{index}", "dep", alternatives=0)
    clock.advance(TTL + 1)

    attempts.record("fresh", "dep", alternatives=0)

    assert attempts.size() == 1


def test_memory_is_bounded_by_dropping_the_oldest(clock: FakeClock) -> None:
    attempts = tracker(clock, max_requests=3)
    for index in range(5):
        attempts.record(f"r{index}", "dep", alternatives=0)
        clock.advance(1)

    assert (attempts.size(), attempts.attempted("r0"), attempts.attempted("r4")) == (
        3,
        frozenset(),
        frozenset({"dep"}),
    )


def test_a_client_supplied_call_id_does_not_join_attempt_sets(clock: FakeClock) -> None:
    attempts = tracker(clock)
    first = {
        "litellm_call_id": "shared-by-client",
        "metadata": {REQUEST_ID_FIELD: issue_request_id()},
    }
    second = {
        "litellm_call_id": "shared-by-client",
        "metadata": {REQUEST_ID_FIELD: issue_request_id()},
    }
    attempts.record(read_request_id(first) or "", "dep-a", alternatives=0)

    assert attempts.attempted(read_request_id(second)) == frozenset()


def test_request_without_a_plugin_id_is_not_tracked_by_the_call_id() -> None:
    assert read_request_id({"litellm_call_id": "abc", "metadata": {}}) is None


def test_issued_ids_are_unique() -> None:
    assert len({issue_request_id() for _ in range(1000)}) == 1000


def test_id_is_read_from_either_metadata_container() -> None:
    chat = {"metadata": {REQUEST_ID_FIELD: "a"}}
    responses = {"litellm_metadata": {REQUEST_ID_FIELD: "b"}}

    assert (read_request_id(chat), read_request_id(responses)) == ("a", "b")


def test_litellm_metadata_wins_when_both_exist() -> None:
    kwargs = {
        "metadata": {REQUEST_ID_FIELD: "a"},
        "litellm_metadata": {REQUEST_ID_FIELD: "b"},
    }

    assert read_request_id(kwargs) == "b"


def test_missing_metadata_reads_as_empty() -> None:
    assert request_metadata({"metadata": None}) == {}
