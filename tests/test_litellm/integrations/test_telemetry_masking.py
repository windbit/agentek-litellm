import asyncio

import pytest

from litellm.integrations.telemetry_masking import (
    CYCLE_PLACEHOLDER,
    MAX_TEXT_CHARS,
    WINDOWS_PER_SPAN_CONCURRENCY,
    TelemetryMasker,
    TelemetryMaskingUnavailable,
    TelemetryTextTooLarge,
)
from litellm.proxy.guardrails.guardrail_hooks.analysis_windows import (
    WINDOW,
    plan_windows,
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

    async def mutating(text, analyze=True):
        payload.pop("c", None)  # конкуррентная мутация источника во время await
        return await original(text)

    masker._mask_text = mutating
    result = await masker.mask(payload)
    assert result == {"a": "x", "b": "y", "c": "z"}


@pytest.mark.asyncio
async def test_cyclic_payload_is_masked(rulebook):
    # Так выглядит блок логирования после ретрая роутера в прокси: запись о неудачной попытке
    # держит тело запроса, а его metadata — тот же словарь, что ссылается на список попыток.
    metadata = {"user_api_key_alias": "ИНН 500100732259"}
    previous_models = [{"proxy_server_request": {"body": {"metadata": metadata}}}]
    metadata["previous_models"] = previous_models
    payload = {"litellm_params": {"metadata": metadata}}

    masked = await build(rulebook).mask(payload)

    masked_metadata = masked["litellm_params"]["metadata"]
    assert masked_metadata["user_api_key_alias"] == "ИНН <RU_INN_1>"
    failed_request = masked_metadata["previous_models"][0]["proxy_server_request"]
    assert failed_request["body"]["metadata"] == CYCLE_PLACEHOLDER


@pytest.mark.asyncio
async def test_repeated_reference_is_masked_in_every_place(rulebook):
    # Один объект под двумя ключами — не цикл: маскируется в обоих местах.
    shared = {"content": "ИНН 500100732259"}
    masked = await build(rulebook).mask({"input": shared, "output": [shared]})
    assert masked["input"] == {"content": "ИНН <RU_INN_1>"}
    assert masked["output"] == [{"content": "ИНН <RU_INN_1>"}]


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

    async def counting(text, analyze=True):
        calls.append(text)
        return await original(text, analyze)

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

    async def counting(text, analyze=True):
        calls.append(text)
        return await original(text, analyze)

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
    task = asyncio.create_task(masker.mask({"content": "Иван Петров"}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Запрос обязан докрутиться, несмотря на отмену вызывающего.
    await asyncio.wait_for(completed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_analysis_is_cached_between_spans(rulebook):
    # Каждый ход несёт всю историю: без кэша между спанами она разбирается заново.
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



@pytest.mark.asyncio
async def test_analyzer_sees_only_conversation_text(rulebook):
    calls = []

    async def counting_analyze(text):
        calls.append(text)
        return []

    masker = TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://analyzer",
        analyze_request=counting_analyze,
    )
    await masker.mask(
        {
            "model": "openai/mock",
            "litellm_call_id": "dc89fe9f-dd1a-404b-a23f-ffb53a07a421",
            "optional_params": {"supported": ["frequency_penalty", "logit_bias"]},
            "messages": [{"content": "Позвони Ивану завтра"}],
        }
    )

    assert calls == ["Позвони Ивану завтра"]


@pytest.mark.asyncio
async def test_rules_still_run_outside_conversation(rulebook):
    async def fail_if_called(text):
        raise AssertionError(f"анализатор не должен звучать вне переписки: {text!r}")

    masker = TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://analyzer",
        analyze_request=fail_if_called,
    )
    masked = await masker.mask({"litellm_params": {"api_key": "AKIA0123456789ABCDEF"}})

    assert masked["litellm_params"]["api_key"] == "<API_KEY_1>"


@pytest.mark.asyncio
async def test_response_obj_is_marked_as_conversation(rulebook):
    calls = []

    async def counting_analyze(text):
        calls.append(text)
        return []

    masker = TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://analyzer",
        analyze_request=counting_analyze,
    )
    await masker.mask("Ответ для Ивана", content=True)

    assert calls == ["Ответ для Ивана"]


@pytest.mark.asyncio
async def test_same_text_outside_conversation_does_not_poison_cache(rulebook):
    calls = []

    async def counting_analyze(text):
        calls.append(text)
        return [{"start": 0, "end": 4, "entity_type": "PERSON", "score": 0.9}]

    masker = TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://analyzer",
        analyze_request=counting_analyze,
    )
    shared: dict = {}
    text = "Иван договорился о встрече"
    await masker.mask({"metadata": {"note": text}}, shared)
    masked = await masker.mask({"messages": [{"content": text}]}, shared)

    assert masked["messages"][0]["content"].startswith("<PERSON_1>")


class _SlowAnalyzerSession:
    """Сессия aiohttp, чей /analyze отвечает только по сигналу: медленный analyzer на пике."""

    def __init__(self):
        self.release = asyncio.Event()
        self.live = 0
        self.peak = 0
        self.calls = 0

    def post(self, url, json):
        session = self

        class _Response:
            status = 200

            async def json(self):
                return []

            async def __aenter__(self):
                session.calls += 1
                session.live += 1
                session.peak = max(session.peak, session.live)
                try:
                    await session.release.wait()
                finally:
                    session.live -= 1
                return self

            async def __aexit__(self, *exc):
                return False

        return _Response()


@pytest.fixture
def slow_analyzer(monkeypatch):
    import litellm.integrations.telemetry_masking as tm

    session = _SlowAnalyzerSession()
    monkeypatch.setattr(tm, "_analyze_session", lambda: session)
    tm._budgets.clear()
    yield session
    tm._budgets.clear()


def analyzer_masker(rulebook):
    return TelemetryMasker(
        rulebook_path=rulebook, entities=["PERSON"], analyzer_base="http://analyzer"
    )


def other_tasks():
    return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]


@pytest.mark.asyncio
async def test_cancelled_spans_do_not_pile_up_behind_analyzer(
    rulebook, slow_analyzer, monkeypatch
):
    # Регресс (#1388): отменённые спаны оставляли задачи с текстом ждать слот анализатора без срока.
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "ANALYZE_MAX_CONCURRENCY", 2)
    masker = analyzer_masker(rulebook)
    spans = [
        asyncio.create_task(masker.mask({"content": f"Иван Петров {i}"})) for i in range(50)
    ]
    await asyncio.sleep(0.05)
    for span in spans:
        span.cancel()
    await asyncio.gather(*spans, return_exceptions=True)
    await asyncio.sleep(0)

    assert slow_analyzer.peak == 2
    assert len(other_tasks()) <= 2
    slow_analyzer.release.set()
    await asyncio.gather(*other_tasks(), return_exceptions=True)
    assert tm._loop_budget().exchanges == 0


@pytest.mark.asyncio
async def test_orphaned_exchange_result_is_cached(rulebook, slow_analyzer):
    masker = analyzer_masker(rulebook)
    span = asyncio.create_task(masker.mask({"content": "Иван Петров"}))
    await asyncio.sleep(0.01)
    span.cancel()
    with pytest.raises(asyncio.CancelledError):
        await span
    slow_analyzer.release.set()
    await asyncio.gather(*other_tasks(), return_exceptions=True)

    await masker.mask({"content": "Иван Петров"})
    assert slow_analyzer.calls == 1


@pytest.mark.asyncio
async def test_concurrent_spans_share_one_analyzer_exchange(rulebook, slow_analyzer):
    masker = analyzer_masker(rulebook)
    spans = [
        asyncio.create_task(masker.mask({"content": "Иван Петров"})) for _ in range(5)
    ]
    await asyncio.sleep(0.01)
    slow_analyzer.release.set()
    await asyncio.gather(*spans)
    assert slow_analyzer.calls == 1


@pytest.mark.asyncio
async def test_span_over_inflight_limit_is_dropped_immediately(
    rulebook, slow_analyzer, monkeypatch
):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "MAX_INFLIGHT_SPANS", 3)
    masker = analyzer_masker(rulebook)
    busy = [
        asyncio.create_task(masker.mask_span({"content": f"Иван {i}"}, None))
        for i in range(3)
    ]
    await asyncio.sleep(0.01)
    with pytest.raises(tm.TelemetryMaskingOverloaded):
        await masker.mask_span({"content": "Пётр"}, None)
    slow_analyzer.release.set()
    await asyncio.gather(*busy)
    assert tm._loop_budget().spans == 0


@pytest.mark.asyncio
async def test_span_over_chars_budget_is_dropped(rulebook, slow_analyzer, monkeypatch):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "MAX_INFLIGHT_CHARS", 1000)
    masker = analyzer_masker(rulebook)
    busy = asyncio.create_task(masker.mask_span({"content": "Иван " * 100}, None))
    await asyncio.sleep(0.01)
    with pytest.raises(tm.TelemetryMaskingOverloaded):
        await masker.mask_span({"content": "Пётр " * 200}, None)
    slow_analyzer.release.set()
    await busy
    assert tm._loop_budget().chars == 0


@pytest.mark.asyncio
async def test_span_deadline_covers_waiting_for_analyzer_slot(
    rulebook, slow_analyzer, monkeypatch
):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "MASK_DEADLINE_SECONDS", 0.05)
    budget = tm._loop_budget()
    for _ in range(tm.ANALYZE_MAX_CONCURRENCY):
        await budget.sem.acquire()
    try:
        with pytest.raises(tm.TelemetryMaskingDeadline):
            await analyzer_masker(rulebook).mask_span({"content": "Иван Петров"}, None)
        assert slow_analyzer.calls == 0
        assert budget.spans == 0
    finally:
        for _ in range(tm.ANALYZE_MAX_CONCURRENCY):
            budget.sem.release()


def test_span_deadline_is_shorter_than_logging_worker_timeout():
    from litellm.constants import LOGGING_WORKER_MAX_TIME_PER_COROUTINE
    from litellm.integrations.telemetry_masking import MASK_DEADLINE_SECONDS

    assert MASK_DEADLINE_SECONDS < LOGGING_WORKER_MAX_TIME_PER_COROUTINE


@pytest.mark.asyncio
async def test_mask_span_masks_payload_and_response(rulebook):
    kwargs, response = await build(rulebook).mask_span(
        {"messages": [{"content": "ИНН 500100732259"}]}, {"text": "ИНН 500100732259"}
    )
    assert "500100732259" not in kwargs["messages"][0]["content"]
    assert "500100732259" not in response["text"]


@pytest.mark.asyncio
async def test_memory_stays_bounded_under_analyzer_overload(
    rulebook, slow_analyzer, monkeypatch
):
    # Нагрузка #1388 в миниатюре: поток крупных спанов через настоящий LoggingWorker при
    # зависшем анализаторе. Память маскировки не должна расти вместе с числом спанов.
    import tracemalloc

    import litellm.integrations.telemetry_masking as tm
    from litellm.litellm_core_utils.logging_worker import LoggingWorker

    monkeypatch.setattr(tm, "MASK_DEADLINE_SECONDS", 0.2)
    masker = analyzer_masker(rulebook)
    dropped = []
    span_chars = 50_000
    spans = 1000

    async def callback(i):
        payload = {"content": f"{i} " + "Иван Петров " * (span_chars // 12)}
        try:
            await masker.mask_span(payload, None)
        except tm.TelemetryMaskingUnavailable as err:
            dropped.append(err.reason)

    worker = LoggingWorker(timeout=0.5, max_queue_size=spans * 2, concurrency=100)
    tracemalloc.start()
    try:
        worker.start()
        for i in range(spans):
            worker.enqueue(callback(i))
        await asyncio.wait_for(worker.flush(), timeout=30)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        await worker.stop()
        slow_analyzer.release.set()
        await asyncio.gather(*other_tasks(), return_exceptions=True)

    all_spans_bytes = spans * span_chars * 2
    assert len(dropped) == spans
    assert set(dropped) <= {"overloaded", "deadline"}
    assert peak < all_spans_bytes / 10
    assert tm._loop_budget().spans == 0


PERSON = "Иван Петров"


class WindowAnalyzer:
    """Фиктивный анализатор: находит PERSON целиком внутри присланного текста.

    Имя, оборванное краем текста, он принимает за находку с более высоким score, как это бывает на краю окна.
    """

    def __init__(self, delay=0.0, fail_on=None):
        self.texts = []
        self.live = 0
        self.peak = 0
        self.delay = delay
        self.fail_on = fail_on

    async def __call__(self, text):
        self.texts.append(text)
        if not text.strip():
            raise TelemetryMaskingUnavailable("analyzer rejects blank text")
        if self.fail_on and self.fail_on in text:
            raise TelemetryMaskingUnavailable("analyzer returned HTTP 500")
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.live -= 1
        found, position = [], text.find(PERSON)
        while position >= 0:
            found.append(
                {
                    "entity_type": "PERSON",
                    "start": position,
                    "end": position + len(PERSON),
                    "score": 0.85,
                }
            )
            position = text.find(PERSON, position + 1)
        if text.rstrip().endswith("Иван"):
            stub = len(text.rstrip()) - len("Иван")
            found.append(
                {"entity_type": "PERSON", "start": stub, "end": stub + 4, "score": 0.9}
            )
        return found


@pytest.fixture
def fresh_budgets():
    import litellm.integrations.telemetry_masking as tm

    tm._budgets.clear()
    yield
    tm._budgets.clear()


def window_masker(analyzer):
    return TelemetryMasker(
        entities=["PERSON"], analyzer_base="http://analyzer", analyze_request=analyzer
    )


def prose(length):
    # Номера делают окна непохожими друг на друга: одинаковые окна делили бы один обмен.
    return "".join(f"слово{number} " for number in range(length // 6 + 1))[:length]


def put(text, value, position):
    return text[:position] + value + text[position + len(value) :]


@pytest.mark.asyncio
async def test_long_text_goes_in_windows_and_name_on_the_seam_is_masked():
    text = prose(100_000)
    person_start = plan_windows(text)[0].end - len("Иван ")
    text = put(text, PERSON, person_start)
    windows = plan_windows(text)
    assert person_start < windows[0].end < person_start + len(PERSON)
    analyzer = WindowAnalyzer()

    masked = await window_masker(analyzer).mask({"content": text})

    assert len(analyzer.texts) == len(windows) > 10
    assert max(len(sent) for sent in analyzer.texts) <= WINDOW
    assert "Иван" not in masked["content"]
    assert "Петров" not in masked["content"]
    assert masked["content"].count("<PERSON_1>") == 1
    assert len(masked["content"]) == len(text) - len(PERSON) + len("<PERSON_1>")


@pytest.mark.asyncio
async def test_each_name_in_a_long_text_is_masked_once():
    text = prose(100_000)
    positions = list(range(500, 99_000, 4_000))
    for position in positions:
        text = put(text, PERSON, position)

    masked = await window_masker(WindowAnalyzer()).mask({"content": text})

    assert PERSON not in masked["content"]
    assert masked["content"].count("<PERSON_") == len(positions)


@pytest.mark.asyncio
async def test_failed_window_drops_the_whole_span():
    text = put(prose(60_000), "СБОЙ", 30_000)
    analyzer = WindowAnalyzer(fail_on="СБОЙ")

    with pytest.raises(TelemetryMaskingUnavailable) as error:
        await window_masker(analyzer).mask_span({"content": text}, None)

    assert error.value.reason == "masking_unavailable"


@pytest.mark.asyncio
async def test_blank_window_is_not_sent_to_the_analyzer():
    text = " " * (WINDOW + 500) + PERSON
    analyzer = WindowAnalyzer()

    masked = await window_masker(analyzer).mask({"content": text})

    assert all(sent.strip() for sent in analyzer.texts)
    assert masked["content"].endswith("<PERSON_1>")


@pytest.mark.asyncio
async def test_windows_are_cached_between_spans():
    text = prose(60_000)
    analyzer = WindowAnalyzer()
    masker = window_masker(analyzer)
    await masker.mask({"content": text})
    sent = len(analyzer.texts)

    await masker.mask({"content": text})

    assert len(analyzer.texts) == sent


@pytest.mark.asyncio
async def test_appended_paragraph_costs_at_most_two_exchanges():
    text = prose(60_000)
    analyzer = WindowAnalyzer()
    masker = window_masker(analyzer)
    await masker.mask({"content": text})
    sent = len(analyzer.texts)

    await masker.mask({"content": text + "\n\nновый абзац"})

    assert len(analyzer.texts) - sent <= 2


@pytest.mark.asyncio
async def test_concurrent_spans_share_window_exchanges():
    text = prose(40_000)
    analyzer = WindowAnalyzer(delay=0.02)
    masker = window_masker(analyzer)

    await asyncio.gather(*(masker.mask({"content": text}) for _ in range(4)))

    assert len(analyzer.texts) == len(plan_windows(text))


@pytest.mark.asyncio
async def test_windows_of_one_span_run_in_parallel_up_to_the_span_limit():
    analyzer = WindowAnalyzer(delay=0.02)

    await window_masker(analyzer).mask({"content": prose(80_000)})

    assert analyzer.peak == WINDOWS_PER_SPAN_CONCURRENCY


@pytest.mark.asyncio
@pytest.mark.parametrize(("length", "exchanges"), [(WINDOW, 1), (WINDOW + 1, 2)])
async def test_window_boundary_decides_between_one_exchange_and_several(
    length, exchanges
):
    text = prose(length)
    analyzer = WindowAnalyzer()

    await window_masker(analyzer).mask({"content": text})

    assert len(analyzer.texts) == exchanges
    assert (analyzer.texts == [text]) == (exchanges == 1)


@pytest.mark.asyncio
async def test_text_needing_too_many_windows_is_dropped_before_any_is_sent(monkeypatch):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "MAX_TEXT_CHARS", 2_000_000)
    analyzer = WindowAnalyzer()

    with pytest.raises(TelemetryTextTooLarge):
        await window_masker(analyzer).mask({"content": prose(1_000_000)})

    assert analyzer.texts == []


@pytest.mark.asyncio
async def test_text_over_limit_is_dropped_before_any_window_is_sent():
    analyzer = WindowAnalyzer()

    with pytest.raises(TelemetryTextTooLarge):
        await window_masker(analyzer).mask({"content": prose(MAX_TEXT_CHARS + 1)})

    assert analyzer.texts == []


@pytest.mark.asyncio
async def test_span_deadline_applies_to_all_windows_together(
    fresh_budgets, monkeypatch
):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "MASK_DEADLINE_SECONDS", 0.05)
    analyzer = WindowAnalyzer(delay=0.3)
    masker = window_masker(analyzer)

    with pytest.raises(tm.TelemetryMaskingDeadline):
        await masker.mask_span({"content": prose(80_000)}, None)

    await asyncio.sleep(1)
    assert tm._loop_budget().spans == 0
    assert tm._loop_budget().exchanges == 0


@pytest.mark.asyncio
async def test_window_concurrency_follows_analyzer_slots(fresh_budgets, monkeypatch):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "ANALYZE_MAX_CONCURRENCY", 1)
    masker = window_masker(WindowAnalyzer(delay=0.01))
    live = peak = 0
    analyze_window = masker._analyze_window

    async def counting(text):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            return await analyze_window(text)
        finally:
            live -= 1

    masker._analyze_window = counting
    await masker.mask({"content": prose(80_000)})

    assert peak == 1


@pytest.mark.asyncio
async def test_long_entity_cut_by_window_end_is_masked_whole():
    long_name = "Иван " + "Очень" * 130 + " Петров"
    text = prose(60_000)
    windows = plan_windows(text)
    text = put(text, long_name, windows[0].end - 300)

    async def whole_entities_only(sent):
        position = sent.find(long_name)
        if position < 0:
            return []
        return [
            {
                "entity_type": "PERSON",
                "start": position,
                "end": position + len(long_name),
            }
        ]

    masked = await window_masker(whole_entities_only)._mask_text(text)

    assert "Очень" not in masked
    assert masked.count("<PERSON_") == 1


@pytest.mark.asyncio
async def test_invalid_span_from_analyzer_drops_the_span():
    async def broken(sent):
        return [{"entity_type": "PERSON", "start": 5, "end": len(sent) + 10}]

    with pytest.raises(TelemetryMaskingUnavailable) as error:
        await window_masker(broken).mask_span({"content": prose(30_000)}, None)

    assert error.value.reason == "masking_unavailable"


@pytest.mark.asyncio
async def test_span_cache_is_bounded_by_entries(monkeypatch):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "SPAN_CACHE_SIZE", 2)
    analyzer = WindowAnalyzer()
    masker = window_masker(analyzer)
    for name in ("Первый", "Второй", "Третий"):
        await masker.mask({"content": f"{name} абзац"})
    sent = len(analyzer.texts)

    await masker.mask({"content": "Первый абзац"})

    assert len(analyzer.texts) == sent + 1


@pytest.mark.asyncio
async def test_span_cache_is_bounded_by_total_spans(monkeypatch):
    import litellm.integrations.telemetry_masking as tm

    monkeypatch.setattr(tm, "SPAN_CACHE_MAX_SPANS", 3)
    analyzer = WindowAnalyzer()
    masker = window_masker(analyzer)
    texts = [f"{PERSON} и {PERSON}, абзац {number}" for number in range(3)]
    for text in texts:
        await masker.mask({"content": text})
    sent = len(analyzer.texts)

    await masker.mask({"content": texts[0]})

    assert len(analyzer.texts) == sent + 1
