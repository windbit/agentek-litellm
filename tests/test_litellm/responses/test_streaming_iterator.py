from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator


class _Resp:
    def __init__(self, output):
        self.output = output


class _Completed:
    def __init__(self, response):
        self.response = response


def _make_iterator(streamed_output_items, completed_output):
    iterator = object.__new__(BaseResponsesAPIStreamingIterator)
    iterator._streamed_output_items = streamed_output_items
    iterator.completed_response = _Completed(_Resp(completed_output))
    return iterator


def test_backfill_empty_completed_output_from_output_item_done():
    item = {"id": "msg_1", "type": "message", "content": [{"type": "output_text", "text": "pong"}]}
    iterator = _make_iterator(streamed_output_items=[item], completed_output=[])

    iterator._backfill_empty_completed_output()

    assert iterator.completed_response.response.output == [item]


def test_backfill_is_noop_when_completed_output_already_present():
    existing = [{"id": "real", "type": "message"}]
    iterator = _make_iterator(streamed_output_items=[{"id": "stream"}], completed_output=existing)

    iterator._backfill_empty_completed_output()

    assert iterator.completed_response.response.output == existing


def test_backfill_is_noop_without_streamed_items():
    iterator = _make_iterator(streamed_output_items=[], completed_output=[])

    iterator._backfill_empty_completed_output()

    assert iterator.completed_response.response.output == []
