import asyncio
import random

import pytest

from litellm.proxy.guardrails.guardrail_hooks.analysis_windows import (
    MAX_WINDOWS,
    OVERLAP,
    WINDOW,
    OversizeError,
    SpanLRU,
    Window,
    own_items,
    plan_windows,
    run_windows,
)

UNBOUNDED = 10**6


def random_prose(length: int, seed: int = 7) -> str:
    rng = random.Random(seed)
    words = ["слово", "Иван", "Петров", "заявка", "договор", "a", "bb", "ccc"]
    parts: list[str] = []
    size = 0
    while size < length:
        word = rng.choice(words)
        tail = rng.choice([" ", " ", " ", ", ", ". ", "\n", "\n\n"])
        parts.append(word + tail)
        size += len(word) + len(tail)
    return "".join(parts)[:length]


def assert_valid_plan(text: str, windows: list[Window]) -> None:
    assert windows[0].start == 0
    assert windows[0].own_lo == 0
    assert windows[-1].end == len(text)
    assert windows[-1].own_hi == len(text)
    for window in windows:
        assert window.end - window.start <= WINDOW
        assert window.start <= window.own_lo <= window.own_hi <= window.end
    for left, right in zip(windows, windows[1:]):
        assert left.own_hi == right.own_lo
        assert right.start > left.start
        assert left.end - right.start >= OVERLAP


def with_inserts(length: int, inserts: dict[int, str]) -> str:
    chars = ["a"] * length
    for position, value in inserts.items():
        for offset, char in enumerate(value):
            chars[position + offset] = char
    return "".join(chars)


def test_short_text_is_single_window():
    assert plan_windows("Иван Петров") == [Window(0, 11, 0, 11)]


def test_empty_text_is_single_empty_window():
    assert plan_windows("") == [Window(0, 0, 0, 0)]


def test_text_of_exactly_window_size_is_one_window():
    text = "а" * WINDOW
    assert plan_windows(text) == [Window(0, WINDOW, 0, WINDOW)]


def test_text_one_char_over_window_is_two_windows():
    text = "а" * (WINDOW + 1)
    windows = plan_windows(text)
    assert len(windows) == 2
    assert_valid_plan(text, windows)


def test_zones_cover_text_and_windows_respect_bounds():
    text = random_prose(100_000)
    windows = plan_windows(text)
    assert len(windows) > 10
    assert_valid_plan(text, windows)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("x" * 1_000_000, id="no-separators"),
        pytest.param("x" * 500_000 + "\n" + "x" * 500_000, id="one-line-break"),
        pytest.param(
            '{"a":' + ",".join(f'"v{n}":{n}' for n in range(120_000)) + "}",
            id="minified-json",
        ),
    ],
)
def test_planning_makes_progress_on_pathological_text(text):
    windows = plan_windows(text, max_windows=UNBOUNDED)
    assert_valid_plan(text, windows)


def test_minified_json_is_cut_after_structural_char():
    text = ",".join(f'"k{n}":{n}' for n in range(5000))
    for window in plan_windows(text)[:-1]:
        assert text[window.end - 1] in ',;:"}])/&='


@pytest.mark.parametrize(
    ("inserts", "expected_end"),
    [
        pytest.param(
            {5000: "\n\n", 7000: "\n", 7500: ". ", 7800: " ", 7900: ","},
            5002,
            id="blank-line",
        ),
        pytest.param(
            {7000: "\n", 7500: ". ", 7800: " ", 7900: ","}, 7001, id="line-break"
        ),
        pytest.param({7500: ". ", 7800: " ", 7900: ","}, 7502, id="sentence-end"),
        pytest.param({7500: "! ", 7800: " ", 7900: ","}, 7502, id="sentence-end-nbsp"),
        pytest.param({7800: " ", 7900: ","}, 7801, id="space"),
        pytest.param({7800: " ", 7900: ","}, 7801, id="nbsp"),
        pytest.param({7800: "\t", 7900: ","}, 7801, id="tab"),
        pytest.param({7900: ","}, 7901, id="structural"),
        pytest.param({}, WINDOW, id="hard-cut"),
        pytest.param(
            {1000: "\n\n", 3000: " "}, WINDOW, id="separators-in-left-half-ignored"
        ),
        pytest.param(
            {7500: "word.word", 7900: ","}, 7901, id="period-without-whitespace"
        ),
    ],
)
def test_cut_follows_separator_hierarchy(inserts, expected_end):
    text = with_inserts(WINDOW + 1000, inserts)
    assert plan_windows(text)[0].end == expected_end


@pytest.mark.parametrize(
    "inserts",
    [
        pytest.param({WINDOW - 1: "\r\n"}, id="crlf-on-hard-cut"),
        pytest.param({7000: " ", WINDOW - 1: "\r\n"}, id="crlf-on-space-cut"),
    ],
)
def test_cut_never_splits_crlf(inserts):
    text = with_inserts(WINDOW + 1000, inserts)
    end = plan_windows(text)[0].end
    assert text[end - 1 : end + 1] != "\r\n"


def test_next_window_never_starts_between_cr_and_lf():
    text = "а\r\n" * 6000
    for window in plan_windows(text):
        assert text[window.start - 1 : window.start + 1] != "\r\n"


def test_next_window_snaps_back_to_separator_within_limit():
    text = with_inserts(WINDOW + 3000, {7000: "\n\n", 5900: " "})
    first, second = plan_windows(text)[:2]
    assert first.end == 7002
    assert second.start == 5901


def test_next_window_ignores_separator_beyond_snap_limit():
    text = with_inserts(WINDOW + 3000, {7000: "\n\n", 5700: " "})
    first, second = plan_windows(text)[:2]
    assert second.start == first.end - OVERLAP


def test_prefix_windows_survive_appended_paragraph():
    base = random_prose(60_000)
    grown = base + "\n\nНовый абзац про заявку Иван Петров."
    assert plan_windows(grown)[: len(plan_windows(base)) - 1] == plan_windows(base)[:-1]


def test_oversize_after_max_windows():
    stride = WINDOW - OVERLAP
    fits = "x" * (stride * (MAX_WINDOWS - 1) + WINDOW)
    assert len(plan_windows(fits)) == MAX_WINDOWS
    with pytest.raises(OversizeError):
        plan_windows(fits + "x")


def own_start(windows: list[Window], start: int, end: int) -> list[int]:
    """Что вернёт анализатор, который находит сущность только целиком внутри окна."""
    found: list[int] = []
    for window in windows:
        if window.start <= start and end <= window.end:
            local = {"start": start - window.start, "end": end - window.start}
            found.extend(item["start"] for item in own_items(window, [local]))
    return found


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(random_prose(40_000), id="prose"),
        pytest.param("x" * 40_000, id="no-separators"),
    ],
)
def test_entity_up_to_half_overlap_is_found_exactly_once(text):
    windows = plan_windows(text)
    seams = [boundary for window in windows for boundary in (window.start, window.end)]
    lengths = [2, 3, 17, 100, 255, 256, 400, 511, OVERLAP // 2]
    for length in lengths:
        for seam in seams:
            for start in range(
                max(0, seam - length - 5), min(len(text) - length, seam + 5) + 1
            ):
                assert own_start(windows, start, start + length) == [start]


@pytest.mark.parametrize("shift", range(0, 14))
def test_astral_and_cyrillic_entity_on_seam_is_found_once_and_intact(shift):
    entity = "Иван😀Петров"
    filler = "слово 😀 " * 2000
    seam = plan_windows(filler)[0].end
    start = seam - shift
    text = filler[:start] + entity + filler[start + len(entity) :]
    windows = plan_windows(text)
    found = []
    for window in windows:
        position = text.find(entity, window.start, window.end)
        if position >= 0:
            local = {
                "start": position - window.start,
                "end": position - window.start + len(entity),
            }
            found.extend(
                text[item["start"] : item["end"]] for item in own_items(window, [local])
            )
    assert found == [entity]


def test_own_items_shifts_to_global_offsets_and_drops_foreign():
    window = Window(start=100, end=500, own_lo=150, own_hi=400)
    items = [
        {"start": 10, "end": 20, "entity_type": "PERSON"},
        {"start": 50, "end": 60, "entity_type": "PERSON", "score": 0.85},
        {"start": 299, "end": 310, "entity_type": "EMAIL"},
        {"start": 300, "end": 310, "entity_type": "EMAIL"},
    ]
    assert own_items(window, items) == [
        {"start": 150, "end": 160, "entity_type": "PERSON", "score": 0.85},
        {"start": 399, "end": 410, "entity_type": "EMAIL"},
    ]


def test_own_items_skips_malformed_items():
    window = Window(0, 100, 0, 100)
    items = [
        "text",
        None,
        [1, 2],
        {"start": "5", "end": 9},
        {"start": 5, "end": 9.0},
        {"start": 5},
        {"start": True, "end": 9},
        {"start": 5, "end": 9, "entity_type": "OK"},
    ]
    assert own_items(window, items) == [{"start": 5, "end": 9, "entity_type": "OK"}]


def test_own_items_does_not_mutate_input():
    item = {"start": 5, "end": 9, "nested": {"a": 1}}
    items = [item]
    result = own_items(Window(100, 200, 100, 200), items)
    assert item == {"start": 5, "end": 9, "nested": {"a": 1}}
    assert items == [item]
    assert result[0] is not item
    assert result[0]["start"] == 105


class Probe:
    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.cancelled = 0

    async def fetch(
        self, window: Window, hold: float = 0.01, fail_on: int | None = None
    ) -> int:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(hold)
            if window.start == fail_on:
                raise RuntimeError("window failed")
            return window.start
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1


def windows_for(count: int) -> list[Window]:
    return [
        Window(index * 10, index * 10 + 10, index * 10, index * 10 + 10)
        for index in range(count)
    ]


async def test_run_windows_returns_results_in_window_order():
    probe = Probe()
    windows = windows_for(5)
    results = await run_windows(
        windows, probe.fetch, gate=asyncio.Semaphore(5), timeout=5
    )
    assert results == [window.start for window in windows]


async def test_first_error_cancels_remaining_windows():
    probe = Probe()
    windows = windows_for(6)

    async def fetch(window: Window) -> int:
        if window.start == 10:
            await asyncio.sleep(0.01)
            raise RuntimeError("window failed")
        return await probe.fetch(window, hold=30)

    with pytest.raises(RuntimeError, match="window failed"):
        await run_windows(windows, fetch, gate=asyncio.Semaphore(6), timeout=60)
    assert probe.in_flight == 0
    assert probe.cancelled == 5


async def test_timeout_cancels_unfinished_windows():
    probe = Probe()

    async def fetch(window: Window) -> int:
        return await probe.fetch(window, hold=30)

    with pytest.raises(asyncio.TimeoutError):
        await run_windows(
            windows_for(4), fetch, gate=asyncio.Semaphore(4), timeout=0.05
        )
    assert probe.in_flight == 0
    assert probe.cancelled == 4


async def test_timeout_covers_waiting_for_the_gate():
    probe = Probe()
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await run_windows(windows_for(2), probe.fetch, gate=gate, timeout=0.05)
    assert probe.peak == 0


async def test_parent_cancellation_leaves_nothing_in_flight():
    probe = Probe()

    async def fetch(window: Window) -> int:
        return await probe.fetch(window, hold=30)

    parent = asyncio.create_task(
        run_windows(windows_for(4), fetch, gate=asyncio.Semaphore(4), timeout=60)
    )
    await asyncio.sleep(0.02)
    assert probe.in_flight == 4
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert probe.in_flight == 0
    assert probe.cancelled == 4


async def test_gate_bounds_concurrency():
    probe = Probe()
    await run_windows(
        windows_for(12), probe.fetch, gate=asyncio.Semaphore(3), timeout=5
    )
    assert probe.peak == 3


def test_span_lru_evicts_oldest_by_span_sum():
    cache = SpanLRU(max_entries=100, max_spans=5)
    cache.put("a", [{"n": 1}, {"n": 2}])
    cache.put("b", [{"n": 3}, {"n": 4}])
    cache.put("c", [{"n": 5}, {"n": 6}])
    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None
    assert cache.total_spans == 4


def test_span_lru_evicts_by_entry_count_for_empty_values():
    cache = SpanLRU(max_entries=2, max_spans=100)
    cache.put("a", [])
    cache.put("b", [])
    cache.put("c", [])
    assert cache.get("a") is None
    assert len(cache) == 2


def test_span_lru_get_refreshes_recency():
    cache = SpanLRU(max_entries=2, max_spans=100)
    cache.put("a", [])
    cache.put("b", [])
    cache.get("a")
    cache.put("c", [])
    assert cache.get("a") is not None
    assert cache.get("b") is None


def test_span_lru_overwrite_replaces_old_sum():
    cache = SpanLRU(max_entries=100, max_spans=10)
    cache.put("a", [{}] * 6)
    cache.put("a", [{}] * 2)
    assert cache.total_spans == 2
    cache.put("b", [{}] * 8)
    assert cache.get("a") is not None
    assert cache.total_spans == 10


def test_span_lru_overwrite_can_evict_others():
    cache = SpanLRU(max_entries=100, max_spans=10)
    cache.put("a", [{}] * 4)
    cache.put("b", [{}] * 4)
    cache.put("a", [{}] * 8)
    assert cache.get("b") is None
    assert cache.total_spans == 8


def test_span_lru_value_over_limit_is_not_stored_and_drops_stale_entry():
    cache = SpanLRU(max_entries=100, max_spans=3)
    cache.put("a", [{}])
    cache.put("a", [{}] * 4)
    assert cache.get("a") is None
    assert cache.total_spans == 0


def test_span_lru_returns_copies_of_the_list():
    cache = SpanLRU(max_entries=10, max_spans=10)
    stored = [{"n": 1}]
    cache.put("a", stored)
    stored.append({"n": 2})
    cache.get("a").append({"n": 3})
    assert cache.get("a") == [{"n": 1}]
