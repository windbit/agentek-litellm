import asyncio

import pytest

from litellm.integrations.telemetry_masking import (
    MAX_TEXT_CHARS,
    TelemetryMasker,
    TelemetryMaskingUnavailable,
    TelemetryTextTooLarge,
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
async def test_oversized_text_drops_span_without_analysis():
    class FailingRuleEngine:
        def analyze(self, text):
            raise AssertionError("rule engine must not run")

    async def fail_analyze(text):
        raise AssertionError("analyzer must not run")

    masker = TelemetryMasker(
        entities=["PERSON"],
        analyzer_base="http://analyzer",
        rule_engine=FailingRuleEngine(),
        analyze_request=fail_analyze,
    )
    with pytest.raises(TelemetryTextTooLarge) as error:
        await masker.mask({"content": "И" * (MAX_TEXT_CHARS + 1)})
    assert error.value.reason == "text_too_large"


@pytest.mark.asyncio
async def test_oversized_blank_text_drops_span():
    with pytest.raises(TelemetryTextTooLarge):
        await TelemetryMasker(entities=[]).mask({"content": " " * (MAX_TEXT_CHARS + 1)})


@pytest.mark.asyncio
async def test_text_at_limit_is_masked():
    masker = TelemetryMasker(entities=[])
    text = "И" * MAX_TEXT_CHARS

    assert await masker.mask({"content": text}) == {"content": text}


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


def test_dropped_span_counter_increments():
    """Потеря спана обязана быть видна метрикой, а не только строкой в логе."""
    from prometheus_client import REGISTRY

    from litellm.integrations.telemetry_masking import record_dropped_span

    def value():
        return REGISTRY.get_sample_value(
            "litellm_telemetry_spans_dropped_total", {"reason": "masking_unavailable"}
        ) or 0.0

    before = value()
    record_dropped_span("masking_unavailable")
    assert value() == before + 1


def test_dropped_span_counter_never_raises(monkeypatch):
    """Метрика не может быть причиной отказа логирующего колбэка."""
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "_dropped_spans_counter", None, raising=False)
    monkeypatch.setattr(tm, "_dropped_spans_counter_ready", True, raising=False)
    tm.record_dropped_span("masking_unavailable")  # без счётчика — просто no-op


def test_analyze_timeout_is_short_by_default():
    """Регресс-страж: 30с ожидания топили event-loop шлюза и роняли liveness (#1171)."""
    from litellm.integrations.telemetry_masking import ANALYZE_TIMEOUT_SECONDS

    assert ANALYZE_TIMEOUT_SECONDS <= 10


@pytest.mark.asyncio
async def test_analyze_concurrency_semaphore_is_bounded_and_per_loop():
    import litellm.integrations.telemetry_masking as tm

    sem = tm._analyze_semaphore()
    assert sem._value == tm.ANALYZE_MAX_CONCURRENCY
    assert tm._analyze_semaphore() is sem  # тот же loop — тот же семафор


@pytest.mark.asyncio
async def test_analyze_session_is_reused_within_loop():
    # Регресс-страж (#1206): сессия-на-вызов текла коннекторами под отменой logging-worker.
    # Одна сессия на loop переиспользуется вместо создания на каждый вызов анализатора.
    import litellm.integrations.telemetry_masking as tm

    session = tm._analyze_session()
    try:
        assert not session.closed
        assert tm._analyze_session() is session  # тот же loop — та же сессия
    finally:
        await session.close()
        tm._sessions.clear()


@pytest.mark.asyncio
async def test_mask_tolerates_concurrent_source_mutation(rulebook):
    # Регресс (#1206): mask рекурсивно await'ит, уступая loop, а litellm параллельно
    # мутирует живой блок логирования во время итерации → без снимка items это
    # "dictionary changed size during iteration". Снимок обязан пережить мутацию.
    masker = build(rulebook)
    payload = {"a": "x", "b": "y", "c": "z"}
    original = masker._mask_text

    async def mutating(text):
        payload.pop("c", None)  # конкуррентная мутация источника во время await
        return await original(text)

    masker._mask_text = mutating
    result = await masker.mask(payload)
    assert result == {"a": "x", "b": "y", "c": "z"}


@pytest.mark.asyncio
async def test_mask_deduplicates_repeated_strings(rulebook):
    # Регресс (#1206): одна и та же строка встречается в payload многократно
    # (сообщения/ответ дублируются в top-level, standard_logging_object,
    # original_response). Без дедупа каждый дубль анализируется заново — лишние
    # вызовы analyzer и лишние аллокации, из-за которых RSS полз до OOM. Кэш обязан
    # свести анализ повтора к одному разу, не меняя результат.
    masker = build(rulebook)
    calls = []
    original = masker._mask_text

    async def counting(text):
        calls.append(text)
        return await original(text)

    masker._mask_text = counting
    secret = "ключ AKIA0123456789ABCDEF в конфиге"
    payload = {
        "messages": [{"content": secret}, {"content": secret}],
        "standard_logging_object": {"messages": secret},
        "original_response": secret,
        "n": 7,
    }
    masked = await masker.mask(payload)
    assert calls.count(secret) == 1  # проанализирован один раз, а не четырежды
    assert "AKIA0123456789ABCDEF" not in masked["original_response"]
    assert "AKIA0123456789ABCDEF" not in masked["messages"][1]["content"]


@pytest.mark.asyncio
async def test_mask_shared_cache_dedups_across_calls(rulebook):
    # Ответ лежит и в kwargs, и в response_obj; общий кэш на оба вызова mask()
    # анализирует его текст по разу (langfuse_otel передаёт один кэш обоим).
    masker = build(rulebook)
    calls = []
    original = masker._mask_text

    async def counting(text):
        calls.append(text)
        return await original(text)

    masker._mask_text = counting
    response = "ИНН 500100732259 в ответе"
    cache: dict = {}
    await masker.mask({"response": response}, cache)
    await masker.mask({"choices": [{"text": response}]}, cache)
    assert calls.count(response) == 1


@pytest.mark.asyncio
async def test_analyze_request_survives_outer_cancellation(rulebook):
    # Регресс (#1206): logging-worker litellm оборачивает mask() в wait_for и отменяет её
    # на полуслове. Без shield запрос к analyzer рвётся в момент чтения ответа и оставляет
    # зомби-объекты aiohttp (ResponseHandler/StreamReader), которые копятся до OOM. shield
    # обязан докрутить запрос до конца, несмотря на внешнюю отмену.
    masker = TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://analyzer"
    )
    started = asyncio.Event()
    completed = asyncio.Event()

    async def fake_request(text):
        started.set()
        await asyncio.sleep(0.05)
        completed.set()
        return []

    masker._analyze_request = fake_request
    task = asyncio.create_task(masker.mask({"m": "Иван Петров"}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Запрос обязан докрутиться, несмотря на отмену вызывающего.
    await asyncio.wait_for(completed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_analysis_is_cached_between_spans(rulebook):
    # Колбэк логирования получает всю переписку на каждом ходу, поэтому без кэша между
    # спанами системный промпт и старые сообщения разбираются заново столько раз, сколько
    # было ходов: стоимость хода растёт вместе с историей, а телеметрийный анализатор
    # уходит в потолок при копеечном трафике.
    calls = []

    async def counting_analyze(text):
        calls.append(text)
        return []

    masker = TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://analyzer",
        analyze_request=counting_analyze,
    )
    history = "Договорились с Ивановым Иваном Ивановичем по заявке 42."
    for turn in range(5):
        await masker.mask({"messages": [{"content": history}, {"content": f"ход {turn}"}]})

    assert calls.count(history) == 1


@pytest.mark.asyncio
async def test_cache_key_separates_entity_sets(rulebook):
    calls = []

    async def counting_analyze(text):
        calls.append(text)
        return []

    text = "Иванов Иван Иванович"
    for entities in (["PERSON"], ["PERSON", "EMAIL_ADDRESS"]):
        masker = TelemetryMasker(
            rulebook_path=rulebook, entities=entities, analyzer_base="http://analyzer",
            analyze_request=counting_analyze,
        )
        await masker.mask({"messages": [{"content": text}]})

    assert len(calls) == 2  # разный состав сущностей — разный ключ, общий кэш не применим

