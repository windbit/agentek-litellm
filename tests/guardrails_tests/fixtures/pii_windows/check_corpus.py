"""Проверяет корпус: оффсеты сущностей, длины, окна и швы из manifest.json.

Запуск из корня форка: `python tests/guardrails_tests/fixtures/pii_windows/check_corpus.py`.
Модуль окон берётся из `litellm.proxy.guardrails.guardrail_hooks.analysis_windows`;
другой файл с `plan_windows` задаёт `CORPUS_WINDOWS_MODULE=/path/analysis_windows.py`.
Сверку `detector: rules` с движком правил включает `CORPUS_RULEBOOK=/path/rulebook.yaml`.
"""

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

from litellm.proxy.guardrails.guardrail_hooks.json_escaped_text import decode_json_escapes

CORPUS = Path(__file__).parent
WINDOW = 8000


def load_plan_windows():
    path = os.environ.get("CORPUS_WINDOWS_MODULE")
    if path:
        spec = importlib.util.spec_from_file_location("corpus_analysis_windows", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module("litellm.proxy.guardrails.guardrail_hooks.analysis_windows")
    return module.plan_windows


def seam_errors(case, text, windows, entities):
    errors = []
    for seam in case.get("seams", []):
        role, window = seam["role"], windows[seam["window"]]
        entity = entities[seam["entity"]] if "entity" in seam else None
        start = entity.get("decoded_start", entity["start"]) if entity else None
        end = entity.get("decoded_end", entity["end"]) if entity else None
        if role == "window_end_split" or role == "hard_cut_split" or role == "address_split_by_newline":
            ok = start < window["end"] < end
            if role == "hard_cut_split":
                ok = ok and window["end"] == window["start"] + WINDOW
            if role == "address_split_by_newline":
                ok = ok and text[window["end"] - 1] == "\n" and "\n" in text[start:end]
        elif role == "next_window_start":
            ok = start == window["start"]
        elif role == "hard_start_inside_entity":
            ok = start < window["start"] < end
        elif role == "zone_boundary_last_owned":
            ok = start < window["own_hi"] < end
        elif role == "zone_boundary_first_owned":
            ok = start == window["own_hi"]
        elif role == "crlf_pair_straddles_limit":
            a, b = seam["at"]
            ok = text[a:b + 1] == "\r\n" and b == window["start"] + WINDOW
        elif role == "cut_after_crlf":
            a, b = seam["at"]
            ok = text[a:b] == "\r\n" and b == window["end"]
        elif role == "entity_before_cut":
            ok = end == window["end"] - 2
        elif role == "entity_after_cut":
            ok = start == window["end"]
        else:
            ok = False
        if not ok:
            errors.append(f"{case['file']}: seam {role} window {seam['window']} no longer on the seam")
    return errors


def main():
    plan_windows = load_plan_windows()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((CORPUS / "expected_entities.json").read_text(encoding="utf-8"))
    engine = None
    if os.environ.get("CORPUS_RULEBOOK"):
        from litellm.proxy.guardrails.guardrail_hooks.pii_rules import PiiRuleEngine, load_rulebook

        engine = PiiRuleEngine(load_rulebook(os.environ["CORPUS_RULEBOOK"]))
    errors = []
    listed = {case["file"] for case in manifest["cases"]}
    on_disk = {p.name for p in CORPUS.iterdir() if p.suffix in (".txt", ".json")} - {"manifest.json", "expected_entities.json"}
    if listed != on_disk:
        errors.append(f"files differ from manifest: {sorted(listed ^ on_disk)}")
    for case in manifest["cases"]:
        name = case["file"]
        with open(CORPUS / name, encoding="utf-8", newline="") as handle:
            raw = handle.read()
        if len(raw) != case["chars"] or raw.count("\n") != case["newlines"]:
            errors.append(f"{name}: length or newline count differs from manifest")
        decoded = decode_json_escapes(raw)
        text = decoded.text if "decoded_chars" in case else raw
        if "decoded_chars" in case and len(text) != case["decoded_chars"]:
            errors.append(f"{name}: decoded length differs from manifest")
        entities = expected.get(name, [])
        if len(entities) != case["planted"] or [e["start"] for e in entities] != sorted(e["start"] for e in entities):
            errors.append(f"{name}: planted count or order")
        for entity in entities:
            if "decoded_start" in entity:
                a, b = entity["decoded_start"], entity["decoded_end"]
                if decoded.source_span(a, b) != (entity["start"], entity["end"]):
                    errors.append(f"{name}: raw offsets of {entity['text']!r} do not map from decoded ones")
            else:
                a, b = entity["start"], entity["end"]
            if text[a:b] != entity["text"]:
                errors.append(f"{name}: text at {a}:{b} is not {entity['text']!r}")
        windows = [dict(start=w.start, end=w.end, own_lo=w.own_lo, own_hi=w.own_hi) for w in plan_windows(text)]
        if windows != case["windows"]:
            errors.append(f"{name}: windows differ from plan_windows")
        else:
            errors.extend(seam_errors(case, text, windows, entities))
        if engine:
            found = {}
            for span in engine.analyze(text):
                key = (span["start"], span["end"])
                if key not in found or span["score"] > found[key][0]:
                    found[key] = (span["score"], span["entity_type"])
            found = {key: value[1] for key, value in found.items()}
            for entity in entities:
                key = (entity.get("decoded_start", entity["start"]), entity.get("decoded_end", entity["end"]))
                if (entity["detector"] == "rules") != (found.get(key) == entity["entity_type"]):
                    errors.append(f"{name}: detector of {entity['text']!r} differs from the rule engine")
    for error in errors:
        print(error)
    print("corpus ok" if not errors else f"{len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
