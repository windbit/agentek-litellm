"""Раскодирование JSON-эскейпов для анализа с картой позиций обратно в исходную строку."""

from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

ESCAPE_TRIGGER = "\\u"

_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_UNICODE_ESCAPE_LENGTH = len("\\uXXXX")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_HIGH_SURROGATE_FIRST, _HIGH_SURROGATE_LAST = 0xD800, 0xDBFF
_LOW_SURROGATE_FIRST, _LOW_SURROGATE_LAST = 0xDC00, 0xDFFF
_ASTRAL_BASE = 0x10000


@dataclass(frozen=True)
class DecodedText:
    text: str
    # Отрезками, а не по записи на символ: внутри отрезка позиции идут подряд, новый начинается на эскейпе.
    segment_starts: Sequence[int]
    segment_sources: Sequence[int]

    def source_offset(self, index: int) -> int:
        """Индекс в исходной строке, с которого начинается index-й символ (len(text) → длина строки)."""
        segment = bisect_right(self.segment_starts, index) - 1
        return self.segment_sources[segment] + (index - self.segment_starts[segment])

    def source_span(self, start: int, end: int) -> Tuple[int, int]:
        return self.source_offset(start), self.source_offset(end)


def decode_json_escapes(text: str) -> Optional[DecodedText]:
    """Раскодировать эскейпы; None — если \\u нет и текст разбирается как есть."""
    if ESCAPE_TRIGGER not in text:
        return None
    return _decode(text)


def unescape_json_fragment(fragment: str) -> str:
    """Значение отрезка исходной строки. Отрезок должен лежать на границах эскейпов, как спаны из source_span."""
    return _decode(fragment).text


def _decode(text: str) -> DecodedText:
    chunks: List[str] = []
    segment_starts: List[int] = []
    segment_sources: List[int] = []
    decoded_length = 0
    index = 0
    while True:
        escape_at = text.find("\\", index)
        if escape_at < 0:
            break
        if escape_at > index:
            chunks.append(text[index:escape_at])
            segment_starts.append(decoded_length)
            segment_sources.append(index)
            decoded_length += escape_at - index
        decoded = _decode_escape_at(text, escape_at)
        # Одиночный обратный слеш, неизвестный и оборванный эскейп идут в текст как есть.
        char, consumed = decoded if decoded is not None else (text[escape_at], 1)
        chunks.append(char)
        segment_starts.append(decoded_length)
        segment_sources.append(escape_at)
        decoded_length += 1
        index = escape_at + consumed

    if index < len(text):
        chunks.append(text[index:])
        segment_starts.append(decoded_length)
        segment_sources.append(index)
        decoded_length += len(text) - index
    segment_starts.append(decoded_length)
    segment_sources.append(len(text))
    return DecodedText(
        text="".join(chunks),
        segment_starts=segment_starts,
        segment_sources=segment_sources,
    )


def _decode_escape_at(text: str, index: int) -> Optional[Tuple[str, int]]:
    """(символ, сколько символов исходной строки он занял) или None, если это не эскейп."""
    if index + 1 >= len(text):
        return None
    simple = _SIMPLE_ESCAPES.get(text[index + 1])
    if simple is not None:
        return simple, 2
    if text[index + 1] != "u":
        return None

    code_unit = _hex_code_unit(text, index)
    if code_unit is None:
        return None
    if _HIGH_SURROGATE_FIRST <= code_unit <= _HIGH_SURROGATE_LAST:
        low = _hex_code_unit(text, index + _UNICODE_ESCAPE_LENGTH)
        if low is None or not (_LOW_SURROGATE_FIRST <= low <= _LOW_SURROGATE_LAST):
            # Одиночный суррогат символа не даёт — оставляем запись как есть.
            return None
        astral = (
            _ASTRAL_BASE
            + ((code_unit - _HIGH_SURROGATE_FIRST) << 10)
            + (low - _LOW_SURROGATE_FIRST)
        )
        return chr(astral), 2 * _UNICODE_ESCAPE_LENGTH
    if _LOW_SURROGATE_FIRST <= code_unit <= _LOW_SURROGATE_LAST:
        return None
    return chr(code_unit), _UNICODE_ESCAPE_LENGTH


def _hex_code_unit(text: str, index: int) -> Optional[int]:
    digits = text[index + 2 : index + _UNICODE_ESCAPE_LENGTH]
    if not text.startswith(ESCAPE_TRIGGER, index) or len(digits) < 4:
        return None
    if not all(digit in _HEX_DIGITS for digit in digits):
        return None
    return int(digits, 16)
