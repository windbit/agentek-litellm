"""
Окна анализа: длинный текст режется на перекрывающиеся куски, и анализатор не видит ничего длиннее `WINDOW`.

Модуль чистый: сеть, кэши и семафоры процесса остаются у вызывающего.

Находку окна принимает только окно, в чьей зоне владения она начинается. Зоны соседей делятся по середине
перекрытия и вместе покрывают текст без пропусков и пересечений, поэтому для сущностей до `OVERLAP / 2`
символов сравнивать находки по score не нужно: сущность целиком лежит в окне-владельце, и у неё есть левый контекст.
Более длинную сущность окно-владелец может увидеть обрезанной или не увидеть вовсе, а сосед видит целиком;
`merge_windows` берёт у соседа находку, которую владение отбросило, если она не пересекает принятую того же типа.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

WINDOW = 8000
OVERLAP = 1024
MAX_WINDOWS = 128
SNAP_BACK = 256
SPAN_CACHE_MAX_SPANS = 200_000

PARAGRAPH_BREAKS = ("\n\n", "\r\n\r\n")
SENTENCE_END = ".!?…。！？"
STRUCTURAL = ',;:"}])/&='

# `.*` жадный, поэтому match находит последнее вхождение за один проход в C, без цикла по символам.
_NOT_CRLF_SPACE = r"(?!\r\n)\s"
_AFTER_SENTENCE = re.compile(
    rf".*[{re.escape(SENTENCE_END)}]{_NOT_CRLF_SPACE}", re.DOTALL
)
_AFTER_SPACE = re.compile(rf".*{_NOT_CRLF_SPACE}", re.DOTALL)
_AFTER_STRUCTURAL = re.compile(rf".*[{re.escape(STRUCTURAL)}]", re.DOTALL)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Window:
    start: int
    end: int
    own_lo: int
    own_hi: int


class OversizeError(Exception):
    """Текст не помещается в `MAX_WINDOWS` окон."""


class InvalidWindowResponse(ValueError):
    """Ответ анализатора по окну нельзя применить: позиции находки не целые или вне окна."""


class WindowCancelledError(RuntimeError):
    """Обмен по окну отменён изнутри, а не вызывающим: для вызывающего это сбой окна."""


def plan_windows(
    text: str,
    *,
    window: int = WINDOW,
    overlap: int = OVERLAP,
    max_windows: int = MAX_WINDOWS,
) -> list[Window]:
    """Режет текст на окна не длиннее `window` с перекрытием соседей не меньше `overlap`.

    Разрез окна выбирается по тексту до `start + window` включительно, поэтому дописывание в хвост
    не меняет окна префикса.
    """
    length = len(text)
    bounds: list[tuple[int, int]] = []
    start = 0
    while True:
        if length - start <= window:
            bounds.append((start, length))
            break
        end = _cut_before(text, start + window // 2, start + window)
        bounds.append((start, end))
        if len(bounds) >= max_windows:
            raise OversizeError(
                f"text of {length} chars needs more than {max_windows} windows"
            )
        start = _snap_start(text, target=end - overlap, floor=start + 1)
    return [
        Window(
            start=start,
            end=end,
            own_lo=0 if index == 0 else (start + bounds[index - 1][1]) // 2,
            own_hi=(
                length
                if index == len(bounds) - 1
                else (bounds[index + 1][0] + end) // 2
            ),
        )
        for index, (start, end) in enumerate(bounds)
    ]


def own_items(window: Window, items: Sequence[object]) -> list[dict[str, object]]:
    """Находки окна, начало которых лежит в его зоне владения, со сдвигом в координаты всего текста.

    Элемент, не являющийся словарём, пропускается. Словарь с нецелыми позициями или позициями вне окна
    даёт `InvalidWindowResponse`: ответ с такой находкой нельзя считать полным.
    """
    return [item for item in _shifted(window, items) if _is_owned(window, item)]


def merge_windows(
    windows: Sequence[Window], per_window: Sequence[Sequence[object]]
) -> list[dict[str, object]]:
    """Склеивает ответы всех окон в находки всего текста, отсортированные по `(start, end)`.

    Основа — `own_items` каждого окна. Сверх неё сохраняются находки, которые владение отбросило:
    сущность длиннее `OVERLAP / 2` окно-владелец видит обрезанной, а сосед целиком.
    Находка соседа остаётся, если не пересекает принятую находку того же `entity_type`.
    Принятая находка, упёршаяся в край своего окна, считается обрезком
    и заменяется покрывающей её более длинной находкой соседа того же типа;
    на краях всего текста покрыть нечем, поэтому такие находки остаются как есть.
    В итоге находки одного типа не пересекаются.
    """
    if not windows:
        return []
    shifted = [_shifted(window, items) for window, items in zip(windows, per_window)]
    accepted: list[dict[str, object]] = []
    foreign: list[dict[str, object]] = []
    stubs: list[bool] = []
    for window, items in zip(windows, shifted):
        for item in items:
            if _is_owned(window, item):
                accepted.append(item)
                stubs.append(_touches_edge(window, item))
            else:
                foreign.append(item)
    accepted = [
        _replace_stub(item, foreign) if is_stub else item
        for item, is_stub in zip(accepted, stubs)
    ]
    merged = _without_overlaps(accepted, foreign)
    merged.sort(key=lambda item: (item["start"], item["end"]))  # type: ignore[arg-type,return-value]
    return merged


async def run_windows(
    windows: Sequence[Window],
    fetch: Callable[[Window], Awaitable[T]],
    *,
    gate: asyncio.Semaphore,
    timeout: float | None,
) -> list[T]:
    """Запускает `fetch` по окнам параллельно и возвращает результаты в порядке окон.

    `gate` ограничивает число одновременных окон, `timeout` — всё выполнение вместе с ожиданием `gate`.
    Первая ошибка окна и таймаут отменяют остальные; наружу выходит ошибка, частичного результата нет.
    К возврату или исключению ни одно окно не остаётся в работе.
    """

    closing = False

    async def guarded(window: Window) -> T:
        try:
            async with gate:
                return await fetch(window)
        except asyncio.CancelledError:
            if closing:
                raise
            raise WindowCancelledError() from None

    if not windows:
        return []
    tasks = [asyncio.ensure_future(guarded(window)) for window in windows]
    try:
        done, pending = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_EXCEPTION
        )
        for task in tasks:
            error = task.exception() if task in done else None
            if error is not None:
                raise error
        if pending:
            raise asyncio.TimeoutError()
        return [task.result() for task in tasks]
    finally:
        closing = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class SpanLRU:
    """LRU-кэш списков находок, ограниченный числом записей и суммой находок во всех записях.

    Длинное сообщение даёт тысячи находок на запись, поэтому одного числа записей мало.
    """

    def __init__(self, max_entries: int, max_spans: int) -> None:
        self._entries: dict[str, list[dict[str, object]]] = {}
        self._max_entries = max_entries
        self._max_spans = max_spans
        self._total_spans = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def total_spans(self) -> int:
        return self._total_spans

    def values(self) -> list[list[dict[str, object]]]:
        return [list(spans) for spans in self._entries.values()]

    def get(self, key: str) -> list[dict[str, object]] | None:
        spans = self._entries.pop(key, None)
        if spans is None:
            return None
        self._entries[key] = spans
        return list(spans)

    def put(self, key: str, spans: Sequence[dict[str, object]]) -> None:
        self._discard(key)
        if len(spans) > self._max_spans:
            return
        self._entries[key] = list(spans)
        self._total_spans += len(spans)
        while (
            len(self._entries) > self._max_entries
            or self._total_spans > self._max_spans
        ):
            self._discard(next(iter(self._entries)))

    def _discard(self, key: str) -> None:
        spans = self._entries.pop(key, None)
        if spans is not None:
            self._total_spans -= len(spans)


def _shifted(window: Window, items: Sequence[object]) -> list[dict[str, object]]:
    length = window.end - window.start
    shifted: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        start, end = item.get("start"), item.get("end")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= length
        ):
            raise InvalidWindowResponse("window item has invalid start/end")
        shifted.append(
            {**item, "start": start + window.start, "end": end + window.start}
        )
    return shifted


def _is_owned(window: Window, item: Mapping[str, object]) -> bool:
    return window.own_lo <= item["start"] < window.own_hi  # type: ignore[operator]


def _touches_edge(window: Window, item: Mapping[str, object]) -> bool:
    return item["end"] == window.end or item["start"] == window.start


def _overlaps(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return (
        left.get("entity_type") == right.get("entity_type")
        and left["start"] < right["end"]  # type: ignore[operator]
        and right["start"] < left["end"]  # type: ignore[operator]
    )


def _covers(outer: Mapping[str, object], inner: Mapping[str, object]) -> bool:
    return (
        outer.get("entity_type") == inner.get("entity_type")
        and outer["start"] <= inner["start"]  # type: ignore[operator]
        and inner["end"] <= outer["end"]  # type: ignore[operator]
        and (outer["end"] - outer["start"]) > (inner["end"] - inner["start"])  # type: ignore[operator]
    )


def _replace_stub(
    item: dict[str, object], foreign: Sequence[dict[str, object]]
) -> dict[str, object]:
    covering = [other for other in foreign if _covers(other, item)]
    if not covering:
        return item
    return max(covering, key=lambda other: other["end"] - other["start"])  # type: ignore[operator]


def _without_overlaps(
    accepted: Sequence[dict[str, object]], foreign: Sequence[dict[str, object]]
) -> list[dict[str, object]]:
    """Принятые находки целиком, затем находки соседей, не пересекающие уже оставленные того же типа."""
    kept: list[dict[str, object]] = []
    for item in sorted(accepted, key=lambda item: -(item["end"] - item["start"])):  # type: ignore[operator]
        if not any(_overlaps(item, other) for other in kept):
            kept.append(item)
    for item in sorted(foreign, key=lambda item: (item["start"], -item["end"])):  # type: ignore[operator]
        if not any(_overlaps(item, other) for other in kept):
            kept.append(item)
    return kept


def _splits_crlf(text: str, cut: int) -> bool:
    return text[cut - 1 : cut + 1] == "\r\n"


def _cut_before(text: str, lo: int, hi: int) -> int:
    """Позиция разреза в `(lo, hi]` — сразу после разделителя самого высокого уровня."""
    if _splits_crlf(text, hi):
        hi -= 1
    paragraph_cut = max(
        (
            found + len(separator)
            for separator in PARAGRAPH_BREAKS
            if (found := text.rfind(separator, lo, hi)) >= 0
        ),
        default=-1,
    )
    if paragraph_cut >= 0:
        return paragraph_cut
    found = text.rfind("\n", lo, hi)
    if found >= 0:
        return found + 1
    # Знак конца предложения может стоять на `lo - 1`: пробел после него уже в диапазоне разреза.
    for pattern, first in (
        (_AFTER_SENTENCE, lo - 1),
        (_AFTER_SPACE, lo),
        (_AFTER_STRUCTURAL, lo),
    ):
        match = pattern.match(text, first, hi)
        if match:
            return match.end()
    return hi


def _snap_start(text: str, target: int, floor: int) -> int:
    """Начало следующего окна: ближайший разделитель левее `target` не дальше `SNAP_BACK`, иначе `target`."""
    for start in range(target, max(floor, target - SNAP_BACK) - 1, -1):
        previous = text[start - 1]
        if (previous.isspace() or previous in STRUCTURAL) and not _splits_crlf(
            text, start
        ):
            return start
    return target - 1 if _splits_crlf(text, target) else target
