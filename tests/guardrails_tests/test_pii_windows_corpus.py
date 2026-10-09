"""Проверяет корпус `fixtures/pii_windows`: оффсеты, длины, окна, швы и детекторы правил."""

import importlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from litellm.proxy.guardrails.guardrail_hooks.json_escaped_text import decode_json_escapes
from litellm.proxy.guardrails.guardrail_hooks.pii_rules import PiiRuleEngine, load_rulebook

CORPUS = Path(__file__).parent / "fixtures" / "pii_windows"
WINDOW = 8000
WINDOWS_MODULE = "litellm.proxy.guardrails.guardrail_hooks.analysis_windows"
WINDOWS_MODULE_PATH_ENV = "CORPUS_WINDOWS_MODULE"

MANIFEST = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((CORPUS / "expected_entities.json").read_text(encoding="utf-8"))
CASES = {case["file"]: case for case in MANIFEST["cases"]}


def _plan_windows():
    path = os.environ.get(WINDOWS_MODULE_PATH_ENV)
    if path:
        spec = importlib.util.spec_from_file_location("corpus_analysis_windows", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(WINDOWS_MODULE)
    return module.plan_windows


def _read(name):
    with open(CORPUS / name, encoding="utf-8", newline="") as handle:
        return handle.read()


def _analyzed(name):
    raw = _read(name)
    decoded = decode_json_escapes(raw)
    return raw, decoded, (decoded.text if decoded else raw)


def _bounds(entity):
    return entity.get("decoded_start", entity["start"]), entity.get("decoded_end", entity["end"])


@pytest.fixture(scope="module")
def engine():
    return PiiRuleEngine(load_rulebook(str(CORPUS / "rulebook.yaml")))


def test_manifest_lists_exactly_the_corpus_files():
    on_disk = {p.name for p in CORPUS.iterdir() if p.suffix in (".txt", ".json")}
    on_disk -= {"manifest.json", "expected_entities.json"}
    assert set(CASES) == on_disk


@pytest.mark.parametrize("name", sorted(CASES))
def test_lengths_match_manifest(name):
    raw, decoded, text = _analyzed(name)
    case = CASES[name]
    assert (len(raw), raw.count("\n")) == (case["chars"], case["newlines"])
    assert case.get("decoded_chars") == (len(text) if decoded else None)


@pytest.mark.parametrize("name", sorted(CASES))
def test_entity_offsets_match_text(name):
    raw, decoded, text = _analyzed(name)
    entities = EXPECTED.get(name, [])
    assert len(entities) == CASES[name]["planted"]
    assert [e["start"] for e in entities] == sorted(e["start"] for e in entities)
    for entity in entities:
        start, end = _bounds(entity)
        assert text[start:end] == entity["text"]
        if decoded:
            assert "decoded_start" in entity
            assert decoded.source_span(start, end) == (entity["start"], entity["end"])


@pytest.mark.parametrize("name", sorted(CASES))
def test_windows_follow_plan_windows_on_analyzed_text(name):
    _, _, text = _analyzed(name)
    windows = [
        {"start": w.start, "end": w.end, "own_lo": w.own_lo, "own_hi": w.own_hi} for w in _plan_windows()(text)
    ]
    assert windows == CASES[name]["windows"]


def _seam_holds(role, seam, window, entity, text):
    start, end = _bounds(entity) if entity else (None, None)
    if role == "window_end_split":
        return start < window["end"] < end
    if role == "hard_cut_split":
        return start < window["end"] < end and window["end"] == window["start"] + WINDOW
    if role == "address_split_by_newline":
        return start < window["end"] < end and text[window["end"] - 1] == "\n" and "\n" in text[start:end]
    if role == "next_window_start":
        return start == window["start"]
    if role == "hard_start_inside_entity":
        return start < window["start"] < end
    if role == "zone_boundary_last_owned":
        return start < window["own_hi"] < end
    if role == "zone_boundary_first_owned":
        return start == window["own_hi"]
    if role == "crlf_pair_straddles_limit":
        first, last = seam["at"]
        return text[first : last + 1] == "\r\n" and last == window["start"] + WINDOW
    if role == "cut_after_crlf":
        first, last = seam["at"]
        return text[first:last] == "\r\n" and last == window["end"]
    if role == "entity_before_cut":
        return end == window["end"] - 2
    if role == "entity_after_cut":
        return start == window["end"]
    raise AssertionError(f"unknown seam role {role}")


@pytest.mark.parametrize("name", sorted(n for n, c in CASES.items() if c.get("seams")))
def test_seams_sit_on_window_seams(name):
    _, _, text = _analyzed(name)
    case = CASES[name]
    for seam in case["seams"]:
        entity = EXPECTED[name][seam["entity"]] if "entity" in seam else None
        window = case["windows"][seam["window"]]
        assert _seam_holds(seam["role"], seam, window, entity, text), (seam["role"], seam["window"])


@pytest.mark.parametrize("name", sorted(CASES))
def test_rule_detectors_match_rule_engine(name, engine):
    _, _, text = _analyzed(name)
    best = {}
    for span in engine.analyze(text):
        key = (span["start"], span["end"])
        if key not in best or span["score"] > best[key][0]:
            best[key] = (span["score"], span["entity_type"])
    for entity in EXPECTED.get(name, []):
        found = best.get(_bounds(entity), (None, None))[1]
        assert (entity["detector"] == "rules") == (found == entity["entity_type"]), entity["text"]
