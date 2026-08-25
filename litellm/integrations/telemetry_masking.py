"""
Маскирование телеметрии: спаны не должны содержать персональные данные и секреты.

Работает независимо от гардрейлов. Гардрейл — это политика передачи данных провайдеру,
и она осознанно разная по окружениям; трейс к этой политике отношения не имеет. Значение
секрета в спане остаётся значением секрета, даже если запрос ушёл провайдеру без маски.

Живёт в асинхронном колбэке логирования, то есть после того, как ответ отдан, и на время
хода не влияет. Если полный состав слоёв применить нельзя — спан не отправляется: наполовину
замаскированный трейс выглядит обработанным и тем опаснее.
"""

import os
import re
from typing import Any, Dict, List, Optional, Sequence

from litellm._logging import verbose_logger
from litellm.proxy.guardrails.guardrail_hooks.pii_rules import (
    PiiRuleEngine,
    RulebookError,
    load_rulebook,
)

DEFAULT_GROUPS = ("personal_data", "names", "secrets")
DEFAULT_ENTITIES = ("PERSON",)
ANALYZE_TIMEOUT_SECONDS = 30


class TelemetryMaskingUnavailable(RuntimeError):
    """Полное маскирование невозможно — спан отправлять нельзя."""


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
        self._engine: Optional[PiiRuleEngine] = None
        self._broken = False
        if self.rulebook_path:
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

    async def mask(self, value: Any) -> Any:
        """Рекурсивно маскирует строки в структуре спана, сохраняя её форму."""
        if isinstance(value, str):
            return await self._mask_text(value)
        if isinstance(value, dict):
            return {key: await self.mask(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            masked = [await self.mask(item) for item in value]
            return type(value)(masked) if isinstance(value, tuple) else masked
        return value

    async def _mask_text(self, text: str) -> str:
        if self._broken:
            raise TelemetryMaskingUnavailable("rulebook unusable")
        if not text.strip():
            return text
        spans: List[Dict[str, Any]] = []
        if self._engine is not None:
            spans.extend(self._engine.analyze(text))
        if self.entities and self.analyzer_base:
            spans.extend(await self._analyze(text))
        return self._replace(text, spans)

    async def _analyze(self, text: str) -> List[Dict[str, Any]]:
        import aiohttp

        base = self.analyzer_base or ""
        url = f"{base.rstrip('/')}/analyze"
        payload = {"text": text, "language": self.language, "entities": self.entities}
        try:
            timeout = aiohttp.ClientTimeout(total=ANALYZE_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
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


_masker: Optional[TelemetryMasker] = None


def get_telemetry_masker() -> TelemetryMasker:
    global _masker
    if _masker is None:
        _masker = TelemetryMasker()
    return _masker
