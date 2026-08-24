import pytest

from litellm.proxy.guardrails.guardrail_hooks.pii_rules import (
    PiiRulebook,
    PiiRuleEngine,
    RulebookError,
    validate_iban,
    validate_inn,
    validate_luhn,
    validate_ogrn,
    validate_snils,
)

RULEBOOK = """
version: test-1
groups:
  - name: personal_data
    rules:
      - rule_id: pii.inn.person
        entity: RU_INN
        regex: '\\b\\d{12}\\b'
        validator: inn
      - rule_id: pii.inn.org
        entity: RU_INN
        regex: '\\b\\d{10}\\b'
        validator: inn
      - rule_id: pii.snils
        entity: RU_SNILS
        regex: '\\b\\d{3}-\\d{3}-\\d{3} \\d{2}\\b'
        validator: snils
      - rule_id: pii.ogrn
        entity: RU_OGRN
        regex: '\\b\\d{13}\\b'
        validator: ogrn
      - rule_id: pii.card
        entity: CREDIT_CARD
        regex: '\\b(?:\\d[ -]?){13,19}\\b'
        validator: luhn
      - rule_id: pii.email
        entity: EMAIL_ADDRESS
        regex: '[\\w.+-]+@[\\w-]+\\.[\\w.]+'
  - name: secrets
    rules:
      - rule_id: secrets.aws
        entity: SECRET
        regex: '\\bAKIA[0-9A-Z]{16}\\b'
"""


def build_engine(groups=None):
    return PiiRuleEngine(PiiRulebook.from_yaml(RULEBOOK), enabled_groups=groups)


def entities(spans):
    return sorted(span["entity_type"] for span in spans)


def matched(text, spans):
    return sorted(text[span["start"] : span["end"]] for span in spans)


class TestValidators:
    def test_inn_10_and_12(self):
        assert validate_inn("7707083893")
        assert validate_inn("500100732259")

    def test_inn_rejects_wrong_checksum(self):
        assert not validate_inn("7707083894")
        assert not validate_inn("500100732250")

    def test_inn_rejects_bare_phone_length(self):
        # Голый десятизначный телефон — та самая коллизия, ради которой заведены контрольные суммы.
        assert not validate_inn("9876789676")

    def test_snils(self):
        assert validate_snils("112-233-445 95")
        assert not validate_snils("112-233-445 96")

    def test_ogrn(self):
        assert validate_ogrn("1027700132195")
        assert not validate_ogrn("1027700132196")

    def test_luhn(self):
        assert validate_luhn("4276 3801 2345 6787")
        assert not validate_luhn("4276 3801 2345 6789")

    def test_iban(self):
        assert validate_iban("DE89370400440532013000")
        assert not validate_iban("DE89370400440532013001")


class TestRulebook:
    def test_unknown_validator_is_fatal(self):
        with pytest.raises(RulebookError, match="unknown validator"):
            PiiRulebook.from_yaml(
                "groups:\n  - name: g\n    rules:\n      - rule_id: r\n        entity: E\n"
                "        regex: 'x'\n        validator: nosuch\n"
            )

    def test_invalid_regex_is_fatal(self):
        with pytest.raises(RulebookError, match="invalid regex"):
            PiiRulebook.from_yaml(
                "groups:\n  - name: g\n    rules:\n      - rule_id: r\n        entity: E\n        regex: '('\n"
            )

    def test_duplicate_rule_id_is_fatal(self):
        with pytest.raises(RulebookError, match="duplicate rule_id"):
            PiiRulebook.from_yaml(
                "groups:\n  - name: g\n    rules:\n"
                "      - rule_id: r\n        entity: E\n        regex: 'a'\n"
                "      - rule_id: r\n        entity: E\n        regex: 'b'\n"
            )

    def test_empty_rulebook_is_fatal(self):
        with pytest.raises(RulebookError, match="no rules"):
            PiiRulebook.from_yaml("version: v\ngroups: []\n")

    def test_broken_yaml_is_fatal(self):
        with pytest.raises(RulebookError, match="not valid YAML"):
            PiiRulebook.from_yaml("groups: [\n")

    def test_version_and_groups_exposed(self):
        book = PiiRulebook.from_yaml(RULEBOOK)
        assert book.version == "test-1"
        assert book.groups == ["personal_data", "secrets"]


class TestEngine:
    def test_valid_identifiers_are_found(self):
        text = "ИНН 500100732259, СНИЛС 112-233-445 95, ОГРН 1027700132195"
        spans = build_engine().analyze(text)
        assert entities(spans) == ["RU_INN", "RU_OGRN", "RU_SNILS"]

    def test_number_with_bad_checksum_is_ignored(self):
        spans = build_engine().analyze("Телефон 9876789676, перезвоните")
        assert spans == []

    def test_card_needs_luhn(self):
        good = build_engine().analyze("карта 4276 3801 2345 6787")
        bad = build_engine().analyze("карта 4276 3801 2345 6789")
        assert entities(good) == ["CREDIT_CARD"]
        assert bad == []

    def test_span_offsets_point_at_the_value(self):
        text = "почта a.smirnova@mail.ru, спасибо"
        spans = build_engine().analyze(text)
        assert matched(text, spans) == ["a.smirnova@mail.ru"]

    def test_group_toggle_off_disables_rules(self):
        text = "ключ AKIA0123456789ABCDEF в конфиге"
        assert entities(build_engine().analyze(text)) == ["SECRET"]
        assert build_engine(groups=["personal_data"]).analyze(text) == []

    def test_empty_group_list_disables_everything(self):
        assert build_engine(groups=[]).analyze("ИНН 500100732259") == []

    def test_entity_filter_narrows_rules(self):
        text = "ИНН 500100732259, почта a@b.ru"
        spans = build_engine().analyze(text, entities=["EMAIL_ADDRESS"])
        assert entities(spans) == ["EMAIL_ADDRESS"]

    def test_empty_text(self):
        assert build_engine().analyze("") == []

    def test_span_shape_matches_analyzer_response(self):
        spans = build_engine().analyze("ИНН 500100732259")
        span = spans[0]
        assert set(span) >= {"entity_type", "start", "end", "score"}
        assert isinstance(span["score"], float)
        assert span["recognition_metadata"]["recognizer_name"] == "pii.inn.person"
