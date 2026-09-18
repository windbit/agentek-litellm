"""Восстановление PII-токенов presidio в ответах /v1/responses, включая события стрима."""

import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Any

_TEXT_DELTA_INDEX_FIELDS = {
    "response.output_text.delta": "content_index",
    "response.reasoning_summary_text.delta": "summary_index",
}
_ARGUMENTS_DELTA = "response.function_call_arguments.delta"
_ARGUMENTS_DONE = "response.function_call_arguments.done"
_TEXT_DONE_EVENTS = (
    "response.output_text.done",
    "response.reasoning_summary_text.done",
)
_PART_EVENTS = (
    "response.content_part.added",
    "response.content_part.done",
    "response.reasoning_summary_part.added",
    "response.reasoning_summary_part.done",
)
_ITEM_EVENTS = ("response.output_item.added", "response.output_item.done")
_TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")

_TextKey = tuple[str, str | None, int | None]

_NEVER_MATCHES = "(?!)"


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _set_field(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def _event_type(event: Any) -> str:
    event_type = _field(event, "type")
    return getattr(event_type, "value", event_type)


class PiiTokenRestorer:
    def __init__(self, pii_tokens: dict[str, str]) -> None:
        self._pii_tokens = pii_tokens
        # Один проход по всем токенам: значение, похожее на токен, дальше не подставляется.
        self._pattern = re.compile(
            "|".join(
                re.escape(token) for token in sorted(pii_tokens, key=len, reverse=True)
            )
            or _NEVER_MATCHES
        )
        self._unfinished_tokens = frozenset(
            token[:end] for token in pii_tokens for end in range(1, len(token))
        )
        self._longest_unfinished = max(map(len, self._unfinished_tokens), default=0)

    def restore_text(self, text: str) -> str:
        return self._pattern.sub(lambda match: self._pii_tokens[match.group(0)], text)

    def restore_json(self, text: str) -> str:
        """Аргументы тулов: значение с кавычками или переводом строки не должно ломать JSON."""
        if not self._pattern.search(text):
            return text
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return self.restore_text(text)
        if not isinstance(parsed, (dict, list, str)):
            return self.restore_text(text)
        return json.dumps(self._restore_json_value(parsed), ensure_ascii=False)

    def _restore_json_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                self._restore_json_value(key): self._restore_json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._restore_json_value(item) for item in value]
        if isinstance(value, str):
            return self.restore_json(value)
        return value

    def split_unfinished_token(self, text: str) -> tuple[str, str]:
        """(текст, который можно выдать, хвост — начало известного токена)."""
        for start in range(max(0, len(text) - self._longest_unfinished), len(text)):
            if text[start:] in self._unfinished_tokens:
                return text[:start], text[start:]
        return text, ""


@dataclass
class _HeldText:
    event: Any
    released: str
    tail: str


@dataclass
class _HeldArguments:
    event: Any
    masked_parts: list[str]


class ResponsesStreamUnmasker:
    """
    Событий не добавляет и не удаляет, порядок сохраняет — меняет только поля.
    Текстовая дельта задерживается, пока её хвост может оказаться началом известного токена;
    из дельт аргументов задерживается только последняя: в неё кладутся восстановленные
    аргументы целиком, остальные уходят пустыми.
    """

    def __init__(self, restorer: PiiTokenRestorer) -> None:
        self._restorer = restorer
        self._queue: deque[Any] = deque()
        self._held_texts: dict[_TextKey, _HeldText] = {}
        self._held_arguments: dict[str, _HeldArguments] = {}
        # Одни и те же аргументы приходят в .done, output_item.done и completed — разбираем JSON один раз.
        self._restored_arguments: dict[str, tuple[str, str]] = {}

    def push(self, event: Any) -> list[Any]:
        self._queue.append(event)
        event_type = _event_type(event)
        if event_type in _TEXT_DELTA_INDEX_FIELDS:
            self._on_text_delta(event, event_type)
        elif event_type == _ARGUMENTS_DELTA:
            self._on_arguments_delta(event)
        else:
            self._on_full_value_event(event, event_type)
        return self._release_ready()

    def drain(self) -> list[Any]:
        for key in list(self._held_texts):
            self._flush_text(key)
        for item_id in list(self._held_arguments):
            self._settle_arguments(item_id, None)
        return self._release_ready()

    def _release_ready(self) -> list[Any]:
        held_ids = {id(held.event) for held in self._held_texts.values()} | {
            id(held.event) for held in self._held_arguments.values()
        }
        ready = []
        while self._queue and id(self._queue[0]) not in held_ids:
            ready.append(self._queue.popleft())
        return ready

    def _on_text_delta(self, event: Any, event_type: str) -> None:
        key = (
            event_type,
            _field(event, "item_id"),
            _field(event, _TEXT_DELTA_INDEX_FIELDS[event_type]),
        )
        held = self._held_texts.pop(key, None)
        pending = (held.tail if held else "") + _field(event, "delta")
        releasable, tail = self._restorer.split_unfinished_token(pending)
        released = self._restorer.restore_text(releasable)
        if held is None and not tail:
            _set_field(event, "delta", released)
            return
        if held is None:
            self._held_texts[key] = _HeldText(event, released, tail)
            return
        _set_field(held.event, "delta", held.released + released)
        if tail:
            self._held_texts[key] = _HeldText(event, "", tail)
        else:
            _set_field(event, "delta", "")

    def _flush_text(self, key: _TextKey) -> None:
        held = self._held_texts.pop(key)
        _set_field(held.event, "delta", held.released + held.tail)

    def _on_arguments_delta(self, event: Any) -> None:
        item_id = _field(event, "item_id")
        held = self._held_arguments.get(item_id)
        if held is None:
            self._held_arguments[item_id] = _HeldArguments(
                event, [_field(event, "delta")]
            )
            return
        _set_field(held.event, "delta", "")
        held.event = event
        held.masked_parts.append(_field(event, "delta"))

    def _settle_arguments(self, item_id: str, restored: str | None) -> None:
        held = self._held_arguments.pop(item_id)
        if restored is None:
            restored = self._restore_arguments(item_id, "".join(held.masked_parts))
        _set_field(held.event, "delta", restored)

    def _restore_arguments(self, item_id: str | None, masked: str) -> str:
        cached = self._restored_arguments.get(item_id)
        if cached is not None and cached[0] == masked:
            return cached[1]
        restored = self._restorer.restore_json(masked)
        self._restored_arguments[item_id] = (masked, restored)
        return restored

    def _on_full_value_event(self, event: Any, event_type: str) -> None:
        item = _field(event, "item")
        item_id = _field(event, "item_id") or _field(item, "id")
        for key in [
            key for key in self._held_texts if not item_id or key[1] == item_id
        ]:
            self._flush_text(key)

        if event_type in _TEXT_DONE_EVENTS:
            _set_field(
                event, "text", self._restorer.restore_text(_field(event, "text"))
            )
        elif event_type in _PART_EVENTS:
            self._restore_text_parts([_field(event, "part")])
        elif event_type == _ARGUMENTS_DONE:
            arguments = self._restore_arguments(item_id, _field(event, "arguments"))
            _set_field(event, "arguments", arguments)
            if item_id in self._held_arguments:
                self._settle_arguments(item_id, arguments)
        elif event_type in _ITEM_EVENTS:
            self._restore_item(item)
            if item_id in self._held_arguments:
                self._settle_arguments(item_id, _field(item, "arguments"))
        elif event_type in _TERMINAL_EVENTS:
            for output_item in _field(_field(event, "response"), "output") or []:
                self._restore_item(output_item)
            for held_item_id in list(self._held_arguments):
                self._settle_arguments(held_item_id, None)

    def _restore_item(self, item: Any) -> None:
        arguments = _field(item, "arguments")
        if isinstance(arguments, str):
            _set_field(
                item,
                "arguments",
                self._restore_arguments(_field(item, "id"), arguments),
            )
        custom_tool_input = _field(item, "input")
        if isinstance(custom_tool_input, str):
            _set_field(item, "input", self._restorer.restore_json(custom_tool_input))
        for name in ("content", "summary"):
            self._restore_text_parts(_field(item, name) or [])

    def _restore_text_parts(self, parts: list[Any]) -> None:
        for part in parts:
            text = _field(part, "text")
            if isinstance(text, str):
                _set_field(part, "text", self._restorer.restore_text(text))
