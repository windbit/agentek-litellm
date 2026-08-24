import pytest

from litellm.integrations.telemetry_masking import (
    TelemetryMasker,
    TelemetryMaskingUnavailable,
)

RULEBOOK = """
version: telemetry-test
groups:
  - name: personal_data
    rules:
      - rule_id: pii.inn
        entity: RU_INN
        regex: '\\b\\d{12}\\b'
        validator: inn
  - name: secrets
    rules:
      - rule_id: secrets.aws
        entity: API_KEY
        regex: '\\bAKIA[0-9A-Z]{16}\\b'
"""


@pytest.fixture
def rulebook(tmp_path):
    path = tmp_path / "telemetry-rulebook.yaml"
    path.write_text(RULEBOOK, encoding="utf-8")
    return str(path)


def build(rulebook, **kwargs):
    return TelemetryMasker(rulebook_path=rulebook, entities=[], **kwargs)


@pytest.mark.asyncio
async def test_secret_value_is_masked(rulebook):
    masked = await build(rulebook).mask(
        {"messages": [{"content": "ключ AKIA0123456789ABCDEF в конфиге"}]}
    )
    assert "AKIA0123456789ABCDEF" not in masked["messages"][0]["content"]
    assert "<API_KEY_1>" in masked["messages"][0]["content"]


@pytest.mark.asyncio
async def test_variable_names_survive(rulebook):
    # По именам переменных ведут отладку — маскируем значение, не ключ.
    masked = await build(rulebook).mask({"env": "AWS_ACCESS_KEY_ID=AKIA0123456789ABCDEF"})
    assert masked["env"].startswith("AWS_ACCESS_KEY_ID=")
    assert "AKIA0123456789ABCDEF" not in masked["env"]


@pytest.mark.asyncio
async def test_structure_is_preserved(rulebook):
    payload = {"a": [{"b": "ИНН 500100732259"}], "n": 5, "t": ("x", "ИНН 500100732259")}
    masked = await build(rulebook).mask(payload)
    assert masked["n"] == 5
    assert isinstance(masked["t"], tuple)
    assert "<RU_INN_1>" in masked["a"][0]["b"]


@pytest.mark.asyncio
async def test_clean_text_is_untouched(rulebook):
    payload = {"model": "gpt-5.6-terra", "content": "Проверьте статус заявки, пожалуйста"}
    assert await build(rulebook).mask(payload) == payload


@pytest.mark.asyncio
async def test_analyzer_failure_stops_the_span(rulebook, monkeypatch):
    masker = TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://127.0.0.1:1"
    )
    with pytest.raises(TelemetryMaskingUnavailable):
        await masker.mask({"content": "Смирнова Анна Сергеевна"})


@pytest.mark.asyncio
async def test_masking_is_independent_of_guardrails(rulebook, monkeypatch):
    # Ни одного включённого гардрейла — телеметрия всё равно маскируется.
    monkeypatch.delenv("LITELLM_TELEMETRY_MASKING", raising=False)
    masker = build(rulebook)
    assert masker.configured is True
    masked = await masker.mask({"content": "ИНН 500100732259"})
    assert "500100732259" not in masked["content"]


@pytest.mark.asyncio
async def test_kill_switch(rulebook, monkeypatch):
    monkeypatch.setenv("LITELLM_TELEMETRY_MASKING", "false")
    masker = build(rulebook)
    assert masker.configured is False


@pytest.mark.asyncio
async def test_unusable_rulebook_does_not_raise_on_construction(tmp_path):
    # Колбэк логирования не место для падения: исключение сломало бы телеметрию целиком.
    path = tmp_path / "broken.yaml"
    path.write_text("groups: [\n", encoding="utf-8")
    masker = TelemetryMasker(rulebook_path=str(path), entities=[])
    assert masker.configured is True
    with pytest.raises(TelemetryMaskingUnavailable):
        await masker.mask({"content": "ИНН 500100732259"})


@pytest.mark.asyncio
async def test_missing_rulebook_behaves_as_unavailable(tmp_path):
    masker = TelemetryMasker(rulebook_path=str(tmp_path / "nope.yaml"), entities=[])
    with pytest.raises(TelemetryMaskingUnavailable):
        await masker.mask({"content": "что угодно"})
