"""
Детерминированный слой детекции ПДн: регулярное выражение плюс валидатор контрольной суммы.

Работает в процессе шлюза и не обращается к анализатору, поэтому проход по тексту стоит
десятки миллисекунд там, где NLP-анализ стоит секунды. Отвечает за структурные данные —
ИНН, СНИЛС, ОГРН, КПП, карты, IBAN, контакты, IP, ключи; имена остаются за NLP-слоем.

Правила — данные: YAML-рулбук монтируется в под и правится без пересборки образа.
Валидаторы — код: правило ссылается на валидатор по имени, неизвестное имя роняет загрузку,
иначе контрольная сумма молча перестала бы проверяться.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import yaml

DEFAULT_SCORE = 0.85
# Совпадение, прошедшее контрольную сумму, — не догадка, а проверенный факт, поэтому оно должно
# выигрывать у находки NLP на том же фрагменте: дедуп перекрытий оставляет спан с большим score.
VALIDATED_SCORE = 0.95


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def validate_luhn(value: str) -> bool:
    digits = _digits(value)
    if len(digits) < 12:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def validate_inn(value: str) -> bool:
    digits = _digits(value)
    if len(digits) == 10:
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        checksum = sum(int(digits[i]) * weights[i] for i in range(9)) % 11 % 10
        return checksum == int(digits[9])
    if len(digits) == 12:
        weights_11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        weights_12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        check_11 = sum(int(digits[i]) * weights_11[i] for i in range(10)) % 11 % 10
        check_12 = sum(int(digits[i]) * weights_12[i] for i in range(11)) % 11 % 10
        return check_11 == int(digits[10]) and check_12 == int(digits[11])
    return False


def validate_snils(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 11:
        return False
    # Контрольное число не считается для номеров меньше 001-001-998: так задано в порядке ПФР.
    if int(digits[:9]) < 1001998:
        return False
    total = sum(int(digits[i]) * (9 - i) for i in range(9))
    control = int(digits[9:])
    if total < 100:
        return total == control
    if total in (100, 101):
        return control == 0
    remainder = total % 101
    return (0 if remainder == 100 else remainder) == control


def validate_ogrn(value: str) -> bool:
    digits = _digits(value)
    if len(digits) == 13:
        return int(digits[:12]) % 11 % 10 == int(digits[12])
    if len(digits) == 15:
        return int(digits[:14]) % 13 % 10 == int(digits[14])
    return False


def validate_iban(value: str) -> bool:
    compact = re.sub(r"\s", "", value).upper()
    if len(compact) < 15 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged if ch.isalnum())
    if not numeric:
        return False
    return int(numeric) % 97 == 1


VALIDATORS: Dict[str, Callable[[str], bool]] = {
    "luhn": validate_luhn,
    "inn": validate_inn,
    "snils": validate_snils,
    "ogrn": validate_ogrn,
    "iban": validate_iban,
}


class RulebookError(ValueError):
    """Рулбук не читается или описан неверно — шлюз не должен стартовать с половиной правил."""


@dataclass(frozen=True)
class PiiRule:
    rule_id: str
    group: str
    entity: str
    pattern: "re.Pattern[str]"
    validator: Optional[str]
    capture_group: int
    score: float

    def validate(self, value: str) -> bool:
        if self.validator is None:
            return True
        return VALIDATORS[self.validator](value)


class PiiRulebook:
    """Загруженный набор правил. Версия входит в ключ кэша спанов, поэтому считается по содержимому."""

    def __init__(self, rules: Sequence[PiiRule], version: str) -> None:
        self.rules = list(rules)
        self.version = version

    @property
    def groups(self) -> List[str]:
        return sorted({rule.group for rule in self.rules})

    @classmethod
    def from_yaml(cls, raw: str) -> "PiiRulebook":
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as err:
            raise RulebookError(f"rulebook is not valid YAML: {err}") from err
        if not isinstance(document, dict) or "groups" not in document:
            raise RulebookError("rulebook must be a mapping with a `groups` key")

        rules: List[PiiRule] = []
        seen_ids: Dict[str, str] = {}
        for group in document["groups"] or []:
            group_name = group.get("name")
            if not group_name:
                raise RulebookError("every group needs a `name`")
            for raw_rule in group.get("rules") or []:
                rule = cls._parse_rule(raw_rule, group_name)
                if rule.rule_id in seen_ids:
                    raise RulebookError(f"duplicate rule_id {rule.rule_id}")
                seen_ids[rule.rule_id] = group_name
                rules.append(rule)
        if not rules:
            raise RulebookError("rulebook contains no rules")

        version = document.get("version") or str(abs(hash(raw)))
        return cls(rules, str(version))

    @staticmethod
    def _parse_rule(raw_rule: Dict[str, Any], group_name: str) -> PiiRule:
        for field in ("rule_id", "entity", "regex"):
            if not raw_rule.get(field):
                raise RulebookError(f"rule in group {group_name} is missing `{field}`")
        validator = raw_rule.get("validator")
        if validator is not None and validator not in VALIDATORS:
            raise RulebookError(
                f"rule {raw_rule['rule_id']} references unknown validator `{validator}`"
            )
        try:
            pattern = re.compile(raw_rule["regex"])
        except re.error as err:
            raise RulebookError(f"rule {raw_rule['rule_id']} has an invalid regex: {err}") from err
        return PiiRule(
            rule_id=raw_rule["rule_id"],
            group=group_name,
            entity=raw_rule["entity"],
            pattern=pattern,
            validator=validator,
            capture_group=int(raw_rule.get("capture_group", 0)),
            score=float(raw_rule.get("score", DEFAULT_SCORE)),
        )


class PiiRuleEngine:
    """Прогон рулбука по тексту. Отдаёт спаны в форме ответа анализатора, чтобы дальше по коду
    работали общие дедуп перекрытий, KEEP и нумерованная замена."""

    def __init__(
        self,
        rulebook: PiiRulebook,
        enabled_groups: Optional[Sequence[str]] = None,
    ) -> None:
        self.rulebook = rulebook
        # None — все группы; пустой список — ни одной. Разница значимая: секреты включаются осознанно.
        self.enabled_groups = None if enabled_groups is None else set(enabled_groups)

    def _active_rules(self, entities: Optional[Sequence[str]]) -> List[PiiRule]:
        wanted = set(entities) if entities else None
        return [
            rule
            for rule in self.rulebook.rules
            if (self.enabled_groups is None or rule.group in self.enabled_groups)
            and (wanted is None or rule.entity in wanted)
        ]

    def analyze(
        self,
        text: str,
        entities: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not text:
            return []
        spans: List[Dict[str, Any]] = []
        for rule in self._active_rules(entities):
            for match in rule.pattern.finditer(text):
                group_index = rule.capture_group if rule.capture_group <= (match.lastindex or 0) else 0
                start, end = match.span(group_index)
                if end <= start:
                    continue
                if not rule.validate(match.group(group_index)):
                    continue
                score = max(rule.score, VALIDATED_SCORE) if rule.validator else rule.score
                spans.append(
                    {
                        "entity_type": rule.entity,
                        "start": start,
                        "end": end,
                        "score": score,
                        "analysis_explanation": None,
                        "recognition_metadata": {"recognizer_name": rule.rule_id},
                    }
                )
        return spans


def load_rulebook(path: str) -> PiiRulebook:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as err:
        raise RulebookError(f"cannot read rulebook at {path}: {err}") from err
    return PiiRulebook.from_yaml(raw)
