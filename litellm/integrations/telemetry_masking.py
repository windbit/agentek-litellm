"""
Маскирование телеметрии: спаны не должны содержать персональные данные и секреты.

Работает независимо от гардрейлов. Гардрейл — это политика передачи данных провайдеру,
и она осознанно разная по окружениям; трейс к этой политике отношения не имеет. Значение
секрета в спане остаётся значением секрета, даже если запрос ушёл провайдеру без маски.

Живёт в асинхронном колбэке логирования, то есть после того, как ответ отдан, и на время
хода не влияет. Если полный состав слоёв применить нельзя — спан не отправляется: наполовину
замаскированный трейс выглядит обработанным и тем опаснее.
"""

import asyncio
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Sequence

from litellm._logging import verbose_logger
from litellm.proxy.guardrails.guardrail_hooks.pii_rules import (
    PiiRuleEngine,
    RulebookError,
    load_rulebook,
)

DEFAULT_GROUPS = ("personal_data", "names", "secrets")
DEFAULT_ENTITIES = ("PERSON",)


def _env_number(name: str, default: Any, cast: Any) -> Any:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


# Колбэк логирования не имеет права подвешивать event-loop шлюза. Под спайком нагрузки
# медленный analyzer превращал каждый вызов маскировки в 30с ожидания, спаны копились и
# топили loop — liveness шлюза падал, он рестартовал, ход агентов рвался (инцидент #1171).
# Держим таймаут коротким и ограничиваем число одновременных обращений к анализатору:
# телеметрия важна, но не ценой доступности шлюза — не уложились, спан дропается, ход цел.
ANALYZE_TIMEOUT_SECONDS = _env_number("LITELLM_TELEMETRY_ANALYZE_TIMEOUT", 4.0, float)
ANALYZE_MAX_CONCURRENCY = _env_number("LITELLM_TELEMETRY_MAX_CONCURRENCY", 8, int)
MAX_TEXT_CHARS = _env_number("LITELLM_TELEMETRY_MAX_TEXT_CHARS", 200_000, int)

# Семафор привязан к event-loop, поэтому храним по одному на активный loop: в проде loop
# один и живёт долго, в тестах на каждый прогон свой — общий инстанс тёк бы между loop'ами.
_semaphores: "Dict[Any, asyncio.Semaphore]" = {}


def _analyze_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(ANALYZE_MAX_CONCURRENCY)
        _semaphores[loop] = sem
    return sem


# aiohttp-сессию, как и семафор, держим одну на event-loop. masking зовёт анализатор
# десятки раз на спан, а прежняя сессия-на-вызов плодила TCP-коннекторы, которые под
# отменой logging-worker по таймауту не успевали закрыться и текли в память до OOM (#1206).
# Одна долгоживущая сессия пулит соединения и освобождает их штатно.
_sessions: "Dict[Any, Any]" = {}


def _analyze_session() -> Any:
    import aiohttp

    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is None or session.closed:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=ANALYZE_TIMEOUT_SECONDS)
        )
        _sessions[loop] = session
    return session


class TelemetryMaskingUnavailable(RuntimeError):
    """Полное маскирование невозможно — спан отправлять нельзя."""

    reason = "masking_unavailable"


class TelemetryTextTooLarge(TelemetryMaskingUnavailable):
    reason = "text_too_large"


class RuleEngine(Protocol):
    def analyze(self, text: str) -> List[Dict[str, Any]]: ...


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: Sequence[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class TelemetryMasker:
    """Два слоя над строками спана: детерминированные правила и языковой анализ.

    Языковой слой обязателен: правила берут только канонические ФИО, а обращение по имени,
    редкие и иностранные имена держатся на анализаторе. Поэтому его недоступность —
    не повод отправить спан «как получилось».
    """

    def __init__(
        self,
        rulebook_path: Optional[str] = None,
        groups: Optional[Sequence[str]] = None,
        analyzer_base: Optional[str] = None,
        language: Optional[str] = None,
        entities: Optional[Sequence[str]] = None,
        rule_engine: Optional[RuleEngine] = None,
        analyze_request: Optional[Callable[[str], Awaitable[List[Dict[str, Any]]]]] = None,
    ) -> None:
        self.enabled = _env_flag("LITELLM_TELEMETRY_MASKING", True)
        self.rulebook_path = rulebook_path or os.getenv("LITELLM_TELEMETRY_RULEBOOK")
        self.groups = list(groups) if groups else _env_list(
            "LITELLM_TELEMETRY_RULE_GROUPS", DEFAULT_GROUPS
        )
        self.analyzer_base = (
            analyzer_base
            or os.getenv("LITELLM_TELEMETRY_ANALYZER_BASE")
            or os.getenv("PRESIDIO_ANALYZER_API_BASE")
        )
        self.language = language or os.getenv("LITELLM_TELEMETRY_LANGUAGE", "ru")
        self.entities = list(entities) if entities else _env_list(
            "LITELLM_TELEMETRY_ENTITIES", DEFAULT_ENTITIES
        )
        self._engine = rule_engine
        self._analyze_request_fn = analyze_request
        self._broken = False
        if self.rulebook_path and self._engine is None:
            try:
                self._engine = PiiRuleEngine(
                    load_rulebook(self.rulebook_path), enabled_groups=self.groups
                )
            except RulebookError as err:
                # Здесь нельзя падать, как падает гардрейл: это колбэк логирования, и исключение
                # сломало бы телеметрию целиком, ничего не защитив. Помечаем слой сломанным —
                # дальше он ведёт себя как недоступный, то есть нагрузка спана не уезжает.
                verbose_logger.error("telemetry rulebook unusable: %s", err)
                self._broken = True

    @property
    def configured(self) -> bool:
        return self.enabled and (self._broken or bool(self._engine or self.analyzer_base))

    async def mask(self, value: Any, cache: Optional[Dict[str, str]] = None) -> Any:
        """Рекурсивно маскирует строки в структуре спана, сохраняя её форму.

        `cache` дедуплицирует маскирование одинаковых строк в пределах одного спана.
        Payload спана несёт одну и ту же длинную строку многократно (сообщения и ответ
        лежат разом в top-level, standard_logging_object, original_response и
        complete_streaming_response), поэтому без дедупа mask() рекурсивно анализирует
        каждую копию заново — десятки лишних вызовов analyzer и лишних аллокаций на спан.
        Под живым потоком это и есть остаточный рост RSS до OOM после того, как утечки
        сессий и dict-changed уже закрыты (#1206). Кэш строится на вызов и передаётся
        обоим mask() в колбэке, так что общая нагрузка спана анализируется по разу.
        """
        if cache is None:
            cache = {}
        if isinstance(value, str):
            masked = cache.get(value)
            if masked is None:
                masked = await self._mask_text(value)
                cache[value] = masked
            return masked
        # Снимок контейнера до async-итерации: mask рекурсивно await'ит, уступая loop
        # другим success-колбэкам, а litellm параллельно мутирует живой блок логирования
        # (model_call_details) — итерация по нему на месте падает "dictionary changed size
        # during iteration" и роняет спан (#1206).
        if isinstance(value, dict):
            return {key: await self.mask(item, cache) for key, item in list(value.items())}
        if isinstance(value, (list, tuple)):
            masked_items = [await self.mask(item, cache) for item in list(value)]
            return type(value)(masked_items) if isinstance(value, tuple) else masked_items
        return value

    async def _mask_text(self, text: str) -> str:
        if self._broken:
            raise TelemetryMaskingUnavailable("rulebook unusable")
        if len(text) > MAX_TEXT_CHARS:
            raise TelemetryTextTooLarge(
                f"text length {len(text)} exceeds {MAX_TEXT_CHARS}"
            )
        if not text.strip():
            return text
        spans: List[Dict[str, Any]] = []
        if self._engine is not None:
            spans.extend(self._engine.analyze(text))
        if self.entities and self.analyzer_base:
            spans.extend(await self._analyze(text))
        return self._replace(text, spans)

    async def _analyze(self, text: str) -> List[Dict[str, Any]]:
        # Колбэк логирования litellm оборачивает маскировку в свой wait_for и отменяет её
        # на полуслове, когда analyzer медленный. Если отмена прилетает во время чтения
        # ответа, aiohttp не успевает подчистить протокол соединения — ResponseHandler и
        # StreamReader висят зомби, копятся на каждый оборванный запрос и текут в RSS до OOM
        # (#1206, остаток после shared-session). shield докручивает сам HTTP-обмен до конца
        # (собственный ANALYZE_TIMEOUT его всё равно ограничивает), aiohttp освобождает
        # соединение штатно, а отмена доходит до вызывающего сразу — спан дропается как и был.
        request = self._analyze_request_fn or self._analyze_request
        return await asyncio.shield(request(text))

    async def _analyze_request(self, text: str) -> List[Dict[str, Any]]:
        base = self.analyzer_base or ""
        url = f"{base.rstrip('/')}/analyze"
        payload = {"text": text, "language": self.language, "entities": self.entities}
        try:
            async with _analyze_semaphore():
                session = _analyze_session()
                async with session.post(url, json=payload) as response:
                    if response.status >= 400:
                        raise TelemetryMaskingUnavailable(
                            f"analyzer returned HTTP {response.status}"
                        )
                    body = await response.json()
        except TelemetryMaskingUnavailable:
            raise
        except Exception as err:
            # Тип ошибки, но не текст: в тексте могли быть те самые данные, которые мы прячем.
            raise TelemetryMaskingUnavailable(
                f"analyzer unreachable: {type(err).__name__}"
            ) from err
        if not isinstance(body, list):
            raise TelemetryMaskingUnavailable("analyzer returned an unexpected shape")
        return [item for item in body if isinstance(item, dict)]

    @staticmethod
    def _replace(text: str, spans: List[Dict[str, Any]]) -> str:
        if not spans:
            return text
        accepted: List[Dict[str, Any]] = []
        for span in sorted(
            spans,
            key=lambda s: (s.get("score") or 0, (s.get("end") or 0) - (s.get("start") or 0)),
            reverse=True,
        ):
            start, end = span.get("start"), span.get("end")
            if start is None or end is None or end <= start:
                continue
            if any(start < kept["end"] and kept["start"] < end for kept in accepted):
                continue
            accepted.append({"start": start, "end": end, "entity": span.get("entity_type")})
        accepted.sort(key=lambda s: s["start"])
        out: List[str] = []
        cursor = 0
        counters: Dict[str, int] = {}
        for span in accepted:
            entity = str(span["entity"] or "PII")
            counters[entity] = counters.get(entity, 0) + 1
            out.append(text[cursor : span["start"]])
            out.append(f"<{entity}_{counters[entity]}>")
            cursor = span["end"]
        out.append(text[cursor:])
        return "".join(out)


_dropped_spans_counter = None
_dropped_spans_counter_ready = False


def record_dropped_span(reason: str) -> None:
    """Считает спаны, выброшенные из-за недоступного слоя детекции.

    Потеря телеметрии тихая по замыслу: ход не прерывается, спан просто не уходит.
    Без счётчика заметить это можно только глазами в логе, поэтому факт потери
    выносится в метрику — по ней и настраивается алерт.

    Prometheus в litellm опционален, и метрика не должна быть причиной отказа
    логирующего колбэка: не собралась — молча живём дальше.
    """
    global _dropped_spans_counter, _dropped_spans_counter_ready
    if not _dropped_spans_counter_ready:
        _dropped_spans_counter_ready = True
        try:
            from prometheus_client import Counter

            _dropped_spans_counter = Counter(
                "litellm_telemetry_spans_dropped_total",
                "Spans dropped because telemetry masking could not run",
                ["reason"],
            )
        except Exception as err:  # noqa: BLE001 — метрика не стоит отказа колбэка
            verbose_logger.debug("telemetry masking: no drop counter (%s)", err)
            _dropped_spans_counter = None
    if _dropped_spans_counter is not None:
        _dropped_spans_counter.labels(reason=reason).inc()


_masker: Optional[TelemetryMasker] = None


def get_telemetry_masker() -> TelemetryMasker:
    global _masker
    if _masker is None:
        _masker = TelemetryMasker()
    return _masker
