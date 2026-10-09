"""
Окна анализа: длинный текст режется на перекрывающиеся куски, и анализатор не видит ничего длиннее `WINDOW`.

Разбор в анализаторе сверхлинеен по длине текста, а память растёт с ним же. Окна ограничивают обе вещи
и дают параллелизм. Модуль чистый: сеть, кэши и семафоры процесса остаются у вызывающего.

Находку окна принимает только окно, в чьей зоне владения она начинается. Зоны соседей делятся по середине
перекрытия и вместе покрывают текст без пропусков и пересечений, поэтому сравнивать находки по score не нужно.
Сущность до `OVERLAP / 2` символов, начавшаяся в зоне, целиком лежит в окне, и у неё есть левый контекст.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from litellm._logging import verbose_proxy_logger

WINDOW = 8000
OVERLAP = 1024
MAX_WINDOWS = 128
SNAP_BACK = 256

SENTENCE_END = frozenset(".!?…。！？")
STRUCTURAL = frozenset(',;:"}])/&=')
LINE_BREAKS = ("\n\n", "\n")

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Window:
    start: int
    end: int
    own_lo: int
    own_hi: int


class OversizeError(Exception):
    """Текст не помещается в `MAX_WINDOWS` окон."""


def plan_windows(
    text: str,
    *,
    window: int = WINDOW,
    overlap: int = OVERLAP,
    max_windows: int = MAX_WINDOWS,
) -> list[Window]:
    """Режет текст на окна не длиннее `window` с перекрытием не меньше `overlap`.

    Границы окна зависят только от текста левее разреза: дописывание в хвост не меняет окна префикса.
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
    """Находки окна, начало которых лежит в его зоне владения, со сдвигом в координаты всего текста."""
    owned: list[dict[str, object]] = []
    malformed = 0
    for item in items:
        span = _int_span(item) if isinstance(item, Mapping) else None
        if span is None:
            malformed += 1
            continue
        start, end = span
        if window.own_lo <= window.start + start < window.own_hi:
            owned.append(
                {**item, "start": start + window.start, "end": end + window.start}
            )
    if malformed:
        verbose_proxy_logger.warning(
            "analysis window: skipped %s items without integer start/end", malformed
        )
    return owned


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

    async def guarded(window: Window) -> T:
        async with gate:
            return await fetch(window)

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


def _int_span(item: Mapping[str, object]) -> tuple[int, int] | None:
    start, end = item.get("start"), item.get("end")
    if type(start) is int and type(end) is int:
        return start, end
    return None


def _splits_crlf(text: str, cut: int) -> bool:
    return text[cut - 1 : cut + 1] == "\r\n"


def _cut_before(text: str, lo: int, hi: int) -> int:
    """Позиция разреза в `(lo, hi]` — сразу после разделителя самого высокого уровня."""
    for line_break in LINE_BREAKS:
        found = text.rfind(line_break, lo, hi)
        if found >= 0:
            return found + len(line_break)
    after_space = after_structural = 0
    for cut in range(hi, lo, -1):
        if _splits_crlf(text, cut):
            continue
        previous = text[cut - 1]
        if previous.isspace():
            if text[cut - 2] in SENTENCE_END:
                return cut
            after_space = after_space or cut
        elif previous in STRUCTURAL:
            after_structural = after_structural or cut
    return after_space or after_structural or (hi - 1 if _splits_crlf(text, hi) else hi)


def _snap_start(text: str, target: int, floor: int) -> int:
    """Начало следующего окна: ближайший разделитель левее `target` не дальше `SNAP_BACK`, иначе `target`."""
    for start in range(target, max(floor, target - SNAP_BACK) - 1, -1):
        previous = text[start - 1]
        if (previous.isspace() or previous in STRUCTURAL) and not _splits_crlf(
            text, start
        ):
            return start
    return target
