from litellm.completion_extras.litellm_responses_transformation.transformation import (
    LiteLLMResponsesTransformationHandler,
)
from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator


class _Resp:
    def __init__(self, output):
        self.output = output


class _Completed:
    def __init__(self, response):
        self.response = response


class _CodexOutputItem:
    """Mimics the typed output_item.done item: model_dump yields the codex message dict."""

    def model_dump(self):
        return {
            "id": "msg_1",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "pong", "annotations": []}],
        }


def _make_iterator(streamed_output_items, completed_output):
    iterator = object.__new__(BaseResponsesAPIStreamingIterator)
    iterator._streamed_output_items = streamed_output_items
    iterator.completed_response = _Completed(_Resp(completed_output))
    return iterator


def test_backfill_produces_dict_items_that_convert_to_choices_end_to_end():
    """Codex ships response.completed with output=[]; the backfilled output_item.done must
    survive the responses->chat converter (which only handles dicts / ResponseOutputMessage),
    so the empty output cannot reach the bridge as 'Unknown items ... []'."""
    iterator = _make_iterator(streamed_output_items=[_CodexOutputItem()], completed_output=[])

    iterator._backfill_empty_completed_output()
    output = iterator.completed_response.response.output

    assert len(output) == 1 and isinstance(output[0], dict)

    handler = LiteLLMResponsesTransformationHandler()
    choices = LiteLLMResponsesTransformationHandler._convert_response_output_to_choices(
        output_items=output,
        handle_raw_dict_callback=handler._handle_raw_dict_response_item,
    )

    assert len(choices) == 1
    assert choices[0].message.content == "pong"


def test_backfill_is_noop_when_completed_output_already_present():
    existing = [{"id": "real", "type": "message"}]
    iterator = _make_iterator(streamed_output_items=[_CodexOutputItem()], completed_output=existing)

    iterator._backfill_empty_completed_output()

    assert iterator.completed_response.response.output == existing


def test_backfill_is_noop_without_streamed_items():
    iterator = _make_iterator(streamed_output_items=[], completed_output=[])

    iterator._backfill_empty_completed_output()

    assert iterator.completed_response.response.output == []
