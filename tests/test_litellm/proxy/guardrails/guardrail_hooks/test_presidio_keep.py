"""
Unit tests for the PiiAction.KEEP behaviour in the Presidio guardrail.

KEEP detects an entity only to protect its span: the keep span itself is never
masked or blocked, and it shadows any lower-or-equal-score detection overlapping
it. The motivating case is a date like "07.08.2026" that the greedy Presidio
PhoneRecognizer matches as PHONE_NUMBER (score 0.40) while DateRecognizer matches
it as DATE_TIME (score 0.60); marking DATE_TIME as KEEP lets the date pass through
untouched without raising the phone score threshold (which real phones share).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath("../../../../../.."))

from litellm.proxy.guardrails.guardrail_hooks.presidio import (
    _OPTIONAL_PresidioPIIMasking,
)
from litellm.proxy.guardrails.guardrail_hooks.presidio import BlockedPiiEntityError
from litellm.types.guardrails import PiiAction, PiiEntityType


def _res(entity_type, start, end, score):
    return {"entity_type": entity_type, "start": start, "end": end, "score": score}


def _guardrail(config):
    return _OPTIONAL_PresidioPIIMasking(mock_testing=True, pii_entities_config=config)


def test_keep_drops_keep_span_and_shadowed_overlap():
    # "07.08.2026": DATE_TIME(0.60) overlaps PHONE_NUMBER(0.40) on the same span.
    g = _guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.MASK}
    )
    out = g.apply_keep_entities(
        [_res("DATE_TIME", 0, 10, 0.60), _res("PHONE_NUMBER", 0, 10, 0.40)]
    )
    assert out == []  # date left untouched: nothing to mask


def test_keep_preserves_nonoverlapping_real_phone():
    g = _guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.MASK}
    )
    real_phone = _res("PHONE_NUMBER", 20, 32, 0.60)
    out = g.apply_keep_entities(
        [_res("DATE_TIME", 0, 10, 0.60), _res("PHONE_NUMBER", 0, 10, 0.40), real_phone]
    )
    assert out == [real_phone]  # a genuine phone elsewhere is still masked


def test_keep_does_not_shadow_higher_score_overlap():
    # A higher-confidence entity on the same span must survive the keep span.
    g = _guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PERSON: PiiAction.MASK}
    )
    person = _res("PERSON", 0, 10, 0.85)
    out = g.apply_keep_entities([_res("DATE_TIME", 0, 10, 0.60), person])
    assert out == [person]


def test_keep_noop_without_keep_config():
    g = _guardrail({PiiEntityType.PHONE_NUMBER: PiiAction.MASK})
    results = [_res("PHONE_NUMBER", 0, 12, 0.60)]
    assert g.apply_keep_entities(results) == results


def test_keep_shadows_block_so_date_is_not_blocked():
    # Block guardrail: the shadowed PHONE_NUMBER must not trigger a block on a date.
    g = _guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.BLOCK}
    )
    date_results = g.apply_keep_entities(
        [_res("DATE_TIME", 0, 10, 0.60), _res("PHONE_NUMBER", 0, 10, 0.40)]
    )
    g.raise_exception_if_blocked_entities_detected(date_results)  # must not raise


def test_keep_still_blocks_real_phone():
    g = _guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.BLOCK}
    )
    phone_results = g.apply_keep_entities([_res("PHONE_NUMBER", 8, 20, 0.60)])
    with pytest.raises(BlockedPiiEntityError):
        g.raise_exception_if_blocked_entities_detected(phone_results)
