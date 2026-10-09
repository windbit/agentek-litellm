"""Окна анализа против настоящего анализатора Presidio.

Тесты ходят в живой анализатор и пропускаются без `PRESIDIO_ANALYZER_API_BASE`. Анализатор поднимается так:

    docker run -d --name pii-windows-$$ --memory 2g --memory-swap 2g -p 15002:3000 -e WORKERS=1 \
        -e GUNICORN_CMD_ARGS="--timeout 120 --preload" <образ анализатора>
    PRESIDIO_ANALYZER_API_BASE=http://localhost:15002 PRESIDIO_ANALYZER_CONTAINER=pii-windows-$$ \
        pytest tests/guardrails_tests/test_presidio_windows_integration.py -s

`PRESIDIO_ANALYZER_CONTAINER` нужен только замеру памяти: тест перезапускает этот контейнер. Отчёт о расхождениях с полным разбором печатается в вывод (`-s`).
"""

import asyncio
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest

from litellm.exceptions import GuardrailRaisedException
from litellm.proxy.guardrails.guardrail_hooks import presidio as presidio_module
from litellm.proxy.guardrails.guardrail_hooks.analysis_windows import plan_windows
from litellm.proxy.guardrails.guardrail_hooks.json_escaped_text import (
    decode_json_escapes,
    unescape_json_fragment,
)
from litellm.proxy.guardrails.guardrail_hooks.presidio import (
    _OPTIONAL_PresidioPIIMasking,
)
from litellm.types.guardrails import PiiAction

ANALYZER_BASE = os.environ.get("PRESIDIO_ANALYZER_API_BASE")
ANALYZER_CONTAINER = os.environ.get("PRESIDIO_ANALYZER_CONTAINER")

pytestmark = pytest.mark.skipif(
    not ANALYZER_BASE, reason="PRESIDIO_ANALYZER_API_BASE is not set"
)

CORPUS = Path(__file__).parent / "fixtures" / "pii_windows"
LANGUAGE = "ru"
ANALYZER_ENTITIES = ["PERSON", "LOCATION", "EMAIL_ADDRESS", "PHONE_NUMBER"]
RULE_ENTITIES = ["RU_INN", "RU_SNILS", "RU_PHONE_NUMBER"]
WHOLE_TEXT_TIMEOUT_SECONDS = 900
ANALYZER_START_TIMEOUT_SECONDS = 120
SYNTHETIC_CHARS = 60_000
LONG_MESSAGE_CHARS = 400_000
OVERSIZE_MESSAGE_CHARS = 1_200_000
MAX_WORKER_GROWTH_KIB = 100 * 1024

PERSON_RECALL_MIN = 0.99
EXTRA_SHARE_MAX = 0.01
REGEX_ENTITIES = ("EMAIL_ADDRESS", "PHONE_NUMBER")

FIRST_NAMES = ["Иван", "Мария", "Алексей", "Ольга", "Дмитрий", "Наталья", "Сергей"]
LAST_NAMES = [
    "Петров",
    "Иванова",
    "Смирнов",
    "Кузнецова",
    "Орлов",
    "Волкова",
    "Фёдоров",
]
STREETS = ["Ленина", "Мира", "Советская", "Гагарина", "Новая", "Садовая"]
CITIES = ["Москва", "Казань", "Самара", "Тверь", "Омск", "Тула"]
FILLER = [
    "Совещание перенесли на следующую неделю, документы нужно подготовить заранее.",
    "Отчёт по итогам квартала отправили в бухгалтерию и юристам.",
    "Заказчик просит уточнить сроки поставки и порядок оплаты.",
    "Склад принимает товар по рабочим дням с девяти до шести.",
    "В приложении лежит таблица с расчётами и черновик договора.",
]

_whole_runs: dict[str, list[dict]] = {}


def emit(report: str) -> None:
    sys.stdout.write(report + "\n")


def synthetic_text(chars: int, *, paragraphs: bool, seed: int = 11) -> str:
    """Проза с именами, адресами, email и телефонами; абзацы через пустую строку либо сплошная строка."""
    rng = random.Random(seed)
    blocks: list[str] = []
    size = 0
    while size < chars:
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        sentences = [
            f"Контактное лицо {name} сообщило, что договор подписан.",
            rng.choice(FILLER),
            f"Адрес доставки: {rng.choice(CITIES)}, ул. {rng.choice(STREETS)}, д. {rng.randint(1, 99)}.",
            f"Почта для связи: user{rng.randint(1, 9999)}@example.com.",
            f"Телефон {name.split()[0]}а: +7 9{rng.randint(10, 99)} {rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}.",
            rng.choice(FILLER),
        ]
        block = " ".join(sentences)
        blocks.append(block)
        size += len(block) + 2
    separator = "\n\n" if paragraphs else " "
    return separator.join(blocks)[:chars]


def read_corpus_text(name: str) -> str:
    """Текст так, как его разбирает гардрейл: раскодированный, если в нём есть `\\uXXXX`."""
    with open(CORPUS / name, encoding="utf-8", newline="") as handle:
        raw = handle.read()
    decoded = decode_json_escapes(raw)
    return decoded.text if decoded else raw


def make_guardrail(*, rulebook: bool = False) -> _OPTIONAL_PresidioPIIMasking:
    return _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base=ANALYZER_BASE.rstrip("/") + "/",
        presidio_anonymizer_api_base="http://unused.invalid/",
        pii_entities_config={
            entity: PiiAction.MASK for entity in [*ANALYZER_ENTITIES, *RULE_ENTITIES]
        },
        presidio_language=LANGUAGE,
        output_parse_pii=True,
        guardrail_name="pii",
        default_on=True,
        pii_rulebook=str(CORPUS / "rulebook.yaml") if rulebook else None,
    )


async def analyze_whole(
    guardrail: _OPTIONAL_PresidioPIIMasking, key: str, text: str
) -> list[dict]:
    if key in _whole_runs:
        return _whole_runs[key]
    payload = guardrail._get_presidio_analyze_request_payload(
        text=text, presidio_config=None, request_data={}
    )
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            f"{guardrail.presidio_analyzer_api_base}analyze",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=WHOLE_TEXT_TIMEOUT_SECONDS),
        ) as response,
    ):
        assert response.status == 200, await response.text()
        _whole_runs[key] = await response.json()
    return _whole_runs[key]


async def analyze_windowed(
    guardrail: _OPTIONAL_PresidioPIIMasking, text: str
) -> list[dict]:
    spans = await guardrail._analyze_with_analyzer(
        text=text, presidio_config=None, request_data={}
    )
    assert isinstance(spans, list)
    return spans


def span_key(span: dict) -> tuple:
    return (span["entity_type"], span["start"], span["end"], round(span["score"], 4))


def span_set(spans: list[dict], entity_type: str | None = None) -> set[tuple]:
    return {
        span_key(span)
        for span in spans
        if entity_type is None or span["entity_type"] == entity_type
    }


def describe(text: str, key: tuple) -> str:
    entity_type, start, end, score = key
    return f"{entity_type} [{start}:{end}] score={score} {text[start:end][:60]!r}"


def difference_report(text: str, whole: list[dict], windowed: list[dict]) -> str:
    only_whole = sorted(span_set(whole) - span_set(windowed), key=lambda key: key[1])
    only_windowed = sorted(span_set(windowed) - span_set(whole), key=lambda key: key[1])
    lines = [f"whole={len(whole)} windowed={len(windowed)}"]
    lines += [f"  only whole:    {describe(text, key)}" for key in only_whole[:20]]
    lines += [f"  only windowed: {describe(text, key)}" for key in only_windowed[:20]]
    return "\n".join(lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("paragraphs", [True, False], ids=["paragraphs", "one_line"])
async def test_windowed_findings_equal_whole_text_findings_on_60k(
    paragraphs: bool,
) -> None:
    text = synthetic_text(SYNTHETIC_CHARS, paragraphs=paragraphs)
    guardrail = make_guardrail()
    assert len(plan_windows(text)) > 1

    whole = await analyze_whole(guardrail, f"synthetic-{paragraphs}", text)
    windowed = await analyze_windowed(guardrail, text)

    report = difference_report(text, whole, windowed)
    emit(f"\n[60k paragraphs={paragraphs}] {report}")
    assert span_set(windowed) == span_set(whole), report


def seam_entities() -> list[tuple[str, str, dict]]:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (CORPUS / "expected_entities.json").read_text(encoding="utf-8")
    )
    return [
        (case["file"], seam["role"], expected[case["file"]][seam["entity"]])
        for case in manifest["cases"]
        for seam in case.get("seams", [])
        if "entity" in seam
    ]


def entity_bounds(entity: dict) -> tuple[int, int]:
    return entity.get("decoded_start", entity["start"]), entity.get(
        "decoded_end", entity["end"]
    )


@pytest.mark.asyncio
async def test_entities_on_window_seams_are_found_once_and_whole() -> None:
    guardrail = make_guardrail(rulebook=True)
    by_file: dict[str, list[tuple[str, dict]]] = {}
    for name, role, entity in seam_entities():
        by_file.setdefault(name, []).append((role, entity))
    problems: list[str] = []
    checked = 0

    for name, entities in sorted(by_file.items()):
        text = read_corpus_text(name)
        found = presidio_module._OPTIONAL_PresidioPIIMasking._drop_overlapping_results(
            await guardrail.analyze_text(
                text=text, presidio_config=None, request_data={}
            )
        )
        whole = await analyze_whole(guardrail, name, text)
        for role, entity in entities:
            start, end = entity_bounds(entity)
            overlapping = [
                span
                for span in found
                if span["entity_type"] == entity["entity_type"]
                and span["start"] < end
                and start < span["end"]
            ]
            whole_overlapping = [
                span
                for span in whole
                if span["entity_type"] == entity["entity_type"]
                and span["start"] < end
                and start < span["end"]
            ]
            if entity["detector"] == "analyzer" and not whole_overlapping:
                continue
            checked += 1
            label = f"{name} {role} {entity['entity_type']} [{start}:{end}]"
            if entity["detector"] == "rules":
                if len(overlapping) != 1:
                    problems.append(f"{label}: {len(overlapping)} spans {overlapping}")
                elif overlapping[0]["start"] > start or overlapping[0]["end"] < end:
                    problems.append(f"{label}: cut to {overlapping[0]}")
            elif len(overlapping) != len(span_set(overlapping)) or span_set(
                overlapping
            ) != span_set(whole_overlapping):
                problems.append(
                    f"{label}: windowed {overlapping} vs whole {whole_overlapping}"
                )

    emit(f"\n[seams] checked={checked} problems={len(problems)}")
    for problem in problems:
        emit("  " + problem)
    assert checked > 0
    assert not problems


def contained_strictly(inner: tuple, outer: tuple) -> bool:
    return (
        inner[0] == outer[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and (inner[1], inner[2]) != (outer[1], outer[2])
    )


@pytest.mark.asyncio
async def test_corpus_regression_against_whole_text_analysis() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    guardrail = make_guardrail()
    totals = {
        entity: {"whole": 0, "found": 0, "extra": 0, "cut": 0}
        for entity in ANALYZER_ENTITIES
    }
    lines: list[str] = []

    for case in manifest["cases"]:
        name = case["file"]
        text = read_corpus_text(name)
        whole = await analyze_whole(guardrail, name, text)
        started = time.monotonic()
        windowed = await analyze_windowed(guardrail, text)
        elapsed = time.monotonic() - started
        whole_keys, windowed_keys = span_set(whole), span_set(windowed)
        for entity in ANALYZER_ENTITIES:
            whole_of = {key for key in whole_keys if key[0] == entity}
            windowed_of = {key for key in windowed_keys if key[0] == entity}
            extra = windowed_of - whole_of
            totals[entity]["whole"] += len(whole_of)
            totals[entity]["found"] += len(whole_of & windowed_of)
            totals[entity]["extra"] += len(extra)
            totals[entity]["cut"] += sum(
                any(contained_strictly(key, other) for other in whole_of)
                for key in extra
            )
        lines.append(
            f"{name}: windows={len(case['windows'])} {elapsed:.1f}s "
            f"{difference_report(text, whole, windowed)}"
        )

    emit("\n[corpus]\n" + "\n".join(lines))
    emit("[corpus totals] " + json.dumps(totals, ensure_ascii=False))
    person = totals["PERSON"]
    assert person["whole"] > 0
    assert person["found"] >= PERSON_RECALL_MIN * person["whole"], totals
    assert person["extra"] <= EXTRA_SHARE_MAX * person["whole"], totals
    assert all(entity["cut"] == 0 for entity in totals.values()), totals
    for entity in REGEX_ENTITIES:
        assert totals[entity]["found"] == totals[entity]["whole"], totals


@pytest.mark.asyncio
async def test_rule_entities_of_the_corpus_are_all_found_with_windows() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (CORPUS / "expected_entities.json").read_text(encoding="utf-8")
    )
    guardrail = make_guardrail(rulebook=True)
    missing: list[str] = []
    total = 0

    for case in manifest["cases"]:
        name = case["file"]
        rule_entities = [
            entity for entity in expected.get(name, []) if entity["detector"] == "rules"
        ]
        if not rule_entities:
            continue
        found = await guardrail.analyze_text(
            text=read_corpus_text(name), presidio_config=None, request_data={}
        )
        found_keys = {
            (span["entity_type"], span["start"], span["end"]) for span in found
        }
        for entity in rule_entities:
            total += 1
            start, end = entity_bounds(entity)
            if (entity["entity_type"], start, end) not in found_keys:
                missing.append(f"{name} {entity['entity_type']} [{start}:{end}]")

    emit(f"\n[rules] expected={total} missing={len(missing)} {missing[:10]}")
    assert total > 0
    assert not missing


async def restart_analyzer() -> None:
    """VmHWM только растёт, поэтому замер памяти начинается со свежего воркера."""
    subprocess.run(
        ["docker", "restart", ANALYZER_CONTAINER], check=True, capture_output=True
    )
    deadline = time.monotonic() + ANALYZER_START_TIMEOUT_SECONDS
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(
                    f"{ANALYZER_BASE.rstrip('/')}/health"
                ) as response:
                    if response.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(1)
    raise TimeoutError("analyzer did not come back after restart")


@pytest.mark.asyncio
async def test_escaped_corpus_file_is_masked_at_source_offsets() -> None:
    name = "seam_json_u_escapes_28k.json"
    with open(CORPUS / name, encoding="utf-8", newline="") as handle:
        source = handle.read()
    expected = json.loads(
        (CORPUS / "expected_entities.json").read_text(encoding="utf-8")
    )
    rule_entities = [
        entity for entity in expected[name] if entity["detector"] == "rules"
    ]
    guardrail = make_guardrail(rulebook=True)

    found = await guardrail.analyze_text(
        text=source, presidio_config=None, request_data={}
    )

    found_keys = {(span["entity_type"], span["start"], span["end"]) for span in found}
    assert rule_entities
    for entity in rule_entities:
        assert (entity["entity_type"], entity["start"], entity["end"]) in found_keys
        assert (
            unescape_json_fragment(source[entity["start"] : entity["end"]])
            == entity["text"]
        )


def worker_peaks_kib() -> dict[str, int]:
    """VmHWM каждого процесса gunicorn внутри контейнера анализатора, КиБ."""
    script = (
        "for p in /proc/[0-9]*; do "
        "if grep -qa gunicorn $p/cmdline 2>/dev/null; then "
        "echo $(basename $p) $(grep VmHWM $p/status | tr -dc '0-9 '); fi; done"
    )
    output = subprocess.run(
        ["docker", "exec", ANALYZER_CONTAINER, "sh", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        pid: int(peak) for pid, peak in (line.split() for line in output.splitlines())
    }


@pytest.mark.asyncio
@pytest.mark.skipif(
    not ANALYZER_CONTAINER, reason="PRESIDIO_ANALYZER_CONTAINER is not set"
)
async def test_400k_message_fits_the_deadline_and_keeps_worker_memory_flat() -> None:
    await restart_analyzer()
    guardrail = make_guardrail()
    await analyze_windowed(guardrail, synthetic_text(8_000, paragraphs=True, seed=3))
    before = worker_peaks_kib()
    text = synthetic_text(LONG_MESSAGE_CHARS, paragraphs=True, seed=5)

    started = time.monotonic()
    result = await guardrail.apply_guardrail(
        inputs={"texts": [text]}, request_data={}, input_type="request"
    )
    elapsed = time.monotonic() - started
    after = worker_peaks_kib()

    growth = {pid: after[pid] - before.get(pid, 0) for pid in after}
    emit(
        f"\n[400k] windows={len(plan_windows(text))} {elapsed:.1f}s "
        f"growth_kib={growth} masked={result['texts'][0].count('<PERSON_')}"
    )
    assert elapsed < presidio_module.PRESIDIO_ANALYZE_DEADLINE_SECONDS
    assert "<PERSON_" in result["texts"][0]
    assert max(growth.values()) <= MAX_WORKER_GROWTH_KIB


@pytest.mark.asyncio
async def test_1_2m_message_is_rejected_without_calling_the_analyzer() -> None:
    guardrail = make_guardrail()
    text = synthetic_text(OVERSIZE_MESSAGE_CHARS, paragraphs=True, seed=7)
    posted = []
    original = guardrail._post_window

    async def spy(*args: object, **kwargs: object) -> list[dict]:
        posted.append(args)
        return await original(*args, **kwargs)

    guardrail._post_window = spy

    with pytest.raises(GuardrailRaisedException, match="too long to mask"):
        await guardrail.apply_guardrail(
            inputs={"texts": [text]}, request_data={}, input_type="request"
        )

    assert posted == []
