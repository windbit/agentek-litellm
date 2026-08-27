"""
Unit tests for Presidio PII Masking Guardrail
Tests PII detection and masking for different message formats
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath("../../../../../.."))

import litellm
from litellm.caching.caching import DualCache
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.guardrails.guardrail_hooks.presidio import (
    _OPTIONAL_PresidioPIIMasking,
)
from litellm.exceptions import BlockedPiiEntityError, GuardrailRaisedException
from litellm.types.guardrails import LitellmParams, PiiAction, PiiEntityType
from litellm.types.utils import Choices, Message, ModelResponse


def _make_mock_session_iterator(
    json_response, status=200, content_type="application/json", text_response=""
):
    """Create a mock _get_session_iterator that yields a session returning json_response."""

    @asynccontextmanager
    async def mock_iterator():
        class MockResponse:
            def __init__(self):
                self.status = status
                self.content_type = content_type
                self.headers = {"Content-Type": content_type}

            async def text(self):
                if text_response:
                    return text_response
                import json

                try:
                    return json.dumps(json_response)
                except Exception:
                    return str(json_response)

            async def json(self):
                return json_response

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockSession:
            def post(self, *args, **kwargs):
                self.last_kwargs = kwargs
                return MockResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        yield MockSession()

    return mock_iterator


@pytest.fixture
def presidio_guardrail():
    """Create a Presidio guardrail instance for testing"""
    return _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=False,
        pii_entities_config={
            PiiEntityType.CREDIT_CARD: PiiAction.MASK,
            PiiEntityType.EMAIL_ADDRESS: PiiAction.MASK,
            PiiEntityType.PHONE_NUMBER: PiiAction.MASK,
        },
    )


@pytest.fixture
def mock_user_api_key():
    """Create a mock user API key auth object"""
    return UserAPIKeyAuth(
        api_key="test_key",
        user_id="test_user",
    )


@pytest.fixture
def mock_cache():
    """Create a mock cache object"""
    return MagicMock(spec=DualCache)


@pytest.mark.asyncio
async def test_multimodal_message_format_completion_call_type(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test Presidio PII masking with multimodal message format (content as list)
    for completion call type.

    Tests the message format:
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "My credit card number is 4111-1111-1111-1111..."
            }
        ]
    }
    """
    # Prepare test data with multimodal message format
    test_data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "My credit card number is 4111-1111-1111-1111, my email is test@example.com, and my phone is 555-123-4567",
                    }
                ],
            }
        ],
        "model": "gpt-4",
    }

    # Mock the check_pii method to return redacted text
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        # Simulate PII detection and masking
        redacted_text = text
        redacted_text = redacted_text.replace("4111-1111-1111-1111", "[CREDIT_CARD]")
        redacted_text = redacted_text.replace("test@example.com", "[EMAIL]")
        redacted_text = redacted_text.replace("555-123-4567", "[PHONE]")
        return redacted_text

    presidio_guardrail.check_pii = mock_check_pii

    # Call the async_pre_call_hook with call_type="completion"
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # Verify that PII was masked in the text field
    assert result is not None
    assert "messages" in result
    assert len(result["messages"]) == 1

    message = result["messages"][0]
    assert "content" in message
    assert isinstance(message["content"], list)
    assert len(message["content"]) == 1

    content_item = message["content"][0]
    assert content_item["type"] == "text"
    assert "[CREDIT_CARD]" in content_item["text"]
    assert "[EMAIL]" in content_item["text"]
    assert "[PHONE]" in content_item["text"]

    # Verify original PII is not present
    assert "4111-1111-1111-1111" not in content_item["text"]
    assert "test@example.com" not in content_item["text"]
    assert "555-123-4567" not in content_item["text"]

    print("✓ Multimodal message format test for completion call type passed")


@pytest.mark.asyncio
async def test_multimodal_message_format_anthropic_messages_call_type(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test Presidio PII masking with multimodal message format (content as list)
    for anthropic_messages call type.

    Tests the same message format but with anthropic_messages call type.
    """
    # Prepare test data with multimodal message format
    test_data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "My credit card number is 4111-1111-1111-1111, my email is test@example.com, and my phone is 555-123-4567",
                    }
                ],
            }
        ],
        "model": "claude-3-opus-20240229",
    }

    # Mock the check_pii method to return redacted text
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        # Simulate PII detection and masking
        redacted_text = text
        redacted_text = redacted_text.replace("4111-1111-1111-1111", "[CREDIT_CARD]")
        redacted_text = redacted_text.replace("test@example.com", "[EMAIL]")
        redacted_text = redacted_text.replace("555-123-4567", "[PHONE]")
        return redacted_text

    presidio_guardrail.check_pii = mock_check_pii

    # Call the async_pre_call_hook with call_type="anthropic_messages"
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="anthropic_messages",
    )

    # Verify that PII was masked in the text field
    assert result is not None
    assert "messages" in result
    assert len(result["messages"]) == 1

    message = result["messages"][0]
    assert "content" in message
    assert isinstance(message["content"], list)
    assert len(message["content"]) == 1

    content_item = message["content"][0]
    assert content_item["type"] == "text"
    assert "[CREDIT_CARD]" in content_item["text"]
    assert "[EMAIL]" in content_item["text"]
    assert "[PHONE]" in content_item["text"]

    # Verify original PII is not present
    assert "4111-1111-1111-1111" not in content_item["text"]
    assert "test@example.com" not in content_item["text"]
    assert "555-123-4567" not in content_item["text"]

    print("✓ Multimodal message format test for anthropic_messages call type passed")


@pytest.mark.asyncio
async def test_multimodal_message_multiple_content_items(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test Presidio PII masking with multiple content items in the content list.
    """
    # Prepare test data with multiple content items
    test_data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "My credit card is 4111-1111-1111-1111",
                    },
                    {
                        "type": "text",
                        "text": "My email is test@example.com",
                    },
                ],
            }
        ],
        "model": "gpt-4",
    }

    # Mock the check_pii method
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        redacted_text = text
        redacted_text = redacted_text.replace("4111-1111-1111-1111", "[CREDIT_CARD]")
        redacted_text = redacted_text.replace("test@example.com", "[EMAIL]")
        return redacted_text

    presidio_guardrail.check_pii = mock_check_pii

    # Call the async_pre_call_hook
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # Verify both content items were processed
    assert result is not None
    message = result["messages"][0]
    content_items = message["content"]

    assert len(content_items) == 2
    assert "[CREDIT_CARD]" in content_items[0]["text"]
    assert "[EMAIL]" in content_items[1]["text"]

    print("✓ Multiple content items test passed")


@pytest.mark.asyncio
async def test_mixed_string_and_list_content(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test Presidio PII masking with mixed string and list content formats.
    """
    # Prepare test data with mixed content formats
    test_data = {
        "messages": [
            {
                "role": "user",
                "content": "My credit card is 4111-1111-1111-1111",
            },
            {
                "role": "assistant",
                "content": "I can help you with that.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "My email is test@example.com",
                    }
                ],
            },
        ],
        "model": "gpt-4",
    }

    # Mock the check_pii method
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        redacted_text = text
        redacted_text = redacted_text.replace("4111-1111-1111-1111", "[CREDIT_CARD]")
        redacted_text = redacted_text.replace("test@example.com", "[EMAIL]")
        return redacted_text

    presidio_guardrail.check_pii = mock_check_pii

    # Call the async_pre_call_hook
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # Verify all messages were processed correctly
    assert result is not None
    messages = result["messages"]

    # First message (string content)
    assert isinstance(messages[0]["content"], str)
    assert "[CREDIT_CARD]" in messages[0]["content"]

    # Second message (string content, no PII)
    assert isinstance(messages[1]["content"], str)
    assert messages[1]["content"] == "I can help you with that."

    # Third message (list content)
    assert isinstance(messages[2]["content"], list)
    assert "[EMAIL]" in messages[2]["content"][0]["text"]

    print("✓ Mixed string and list content test passed")


@pytest.mark.asyncio
async def test_content_list_without_text_field(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test Presidio PII masking gracefully handles content items without text field
    (e.g., image content items).
    """
    # Prepare test data with image content (no text field)
    test_data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/image.jpg"},
                    },
                    {
                        "type": "text",
                        "text": "What's in this image? My email is test@example.com",
                    },
                ],
            }
        ],
        "model": "gpt-4-vision",
    }

    # Mock the check_pii method
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        redacted_text = text.replace("test@example.com", "[EMAIL]")
        return redacted_text

    presidio_guardrail.check_pii = mock_check_pii

    # Call the async_pre_call_hook
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # Verify that image content is preserved and text content is processed
    assert result is not None
    content_items = result["messages"][0]["content"]

    assert len(content_items) == 2
    # Image content should remain unchanged
    assert content_items[0]["type"] == "image_url"
    assert content_items[0]["image_url"]["url"] == "https://example.com/image.jpg"

    # Text content should be redacted
    assert content_items[1]["type"] == "text"
    assert "[EMAIL]" in content_items[1]["text"]

    print("✓ Content list without text field test passed")


@pytest.mark.asyncio
async def test_empty_messages(presidio_guardrail, mock_user_api_key, mock_cache):
    """
    Test that Presidio handles empty messages gracefully.
    """
    test_data = {
        "messages": [],
        "model": "gpt-4",
    }

    # Call the async_pre_call_hook
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # Should return data unchanged
    assert result == test_data
    print("✓ Empty messages test passed")


@pytest.mark.asyncio
async def test_no_messages_field(presidio_guardrail, mock_user_api_key, mock_cache):
    """
    Test that Presidio handles missing messages field gracefully.
    """
    test_data = {
        "model": "gpt-4",
        "prompt": "This is a completion request",
    }

    # Call the async_pre_call_hook
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # Should return data unchanged
    assert result == test_data
    print("✓ No messages field test passed")


@pytest.mark.asyncio
async def test_logging_hook_multimodal_message_format(presidio_guardrail):
    """
    Test Presidio async_logging_hook with multimodal message format for completion call type.
    This hook is used to mask PII before logging to external services.
    """
    # Prepare kwargs with multimodal message format
    test_kwargs = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "My credit card number is 4111-1111-1111-1111, my email is test@example.com",
                    }
                ],
            }
        ],
        "model": "gpt-4",
    }

    # Mock result
    mock_result = {"choices": [{"message": {"content": "Response"}}]}

    # Mock the check_pii method
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        redacted_text = text
        redacted_text = redacted_text.replace("4111-1111-1111-1111", "[CREDIT_CARD]")
        redacted_text = redacted_text.replace("test@example.com", "[EMAIL]")
        return redacted_text

    presidio_guardrail.check_pii = mock_check_pii

    # Call the async_logging_hook
    result_kwargs, result_response = await presidio_guardrail.async_logging_hook(
        kwargs=test_kwargs,
        result=mock_result,
        call_type="completion",
    )

    # Verify that PII was masked in the kwargs
    assert result_kwargs is not None
    assert "messages" in result_kwargs
    message = result_kwargs["messages"][0]
    content_item = message["content"][0]

    assert "[CREDIT_CARD]" in content_item["text"]
    assert "[EMAIL]" in content_item["text"]
    assert "4111-1111-1111-1111" not in content_item["text"]
    assert "test@example.com" not in content_item["text"]

    print("✓ Logging hook multimodal message format test passed")


@pytest.mark.asyncio
async def test_logging_hook_multiple_content_items(presidio_guardrail):
    """
    Test Presidio async_logging_hook with multiple content items in a single message.
    """
    test_kwargs = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "My credit card is 4111-1111-1111-1111",
                    },
                    {
                        "type": "text",
                        "text": "My email is test@example.com",
                    },
                ],
            }
        ],
        "model": "gpt-4",
    }

    mock_result = {"choices": [{"message": {"content": "Response"}}]}

    # Mock the check_pii method
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        redacted_text = text
        redacted_text = redacted_text.replace("4111-1111-1111-1111", "[CREDIT_CARD]")
        redacted_text = redacted_text.replace("test@example.com", "[EMAIL]")
        return redacted_text

    presidio_guardrail.check_pii = mock_check_pii

    # Call the async_logging_hook
    result_kwargs, result_response = await presidio_guardrail.async_logging_hook(
        kwargs=test_kwargs,
        result=mock_result,
        call_type="completion",
    )

    # Verify both content items were processed
    message = result_kwargs["messages"][0]
    content_items = message["content"]

    assert len(content_items) == 2
    assert "[CREDIT_CARD]" in content_items[0]["text"]
    assert "[EMAIL]" in content_items[1]["text"]

    print("✓ Logging hook multiple content items test passed")


@pytest.mark.asyncio
async def test_logging_only_does_not_mask_pre_call_request(
    mock_user_api_key, mock_cache
):
    """
    A guardrail configured with `logging_only` must only mask PII for logs/traces,
    never for the request sent to the model. `async_pre_call_hook` should leave the
    request untouched so the model receives (and replies based on) the real input.

    Regression test for the case where the pre-call hook masked the live request,
    causing the model's response to contain anonymization tokens (e.g. <PERSON>)
    instead of the real output.
    """
    presidio_guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        logging_only=True,
        pii_entities_config={PiiEntityType.PHONE_NUMBER: PiiAction.MASK},
    )

    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        return text.replace("555-123-4567", "[PHONE]")

    presidio_guardrail.check_pii = mock_check_pii

    original_text = "My phone is 555-123-4567"
    test_data = {
        "messages": [{"role": "user", "content": original_text}],
        "model": "gpt-4",
    }

    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # The live request must be unchanged: PII reaches the model intact.
    assert result["messages"][0]["content"] == original_text
    assert "[PHONE]" not in result["messages"][0]["content"]

    print("✓ logging_only leaves the pre-call request unmasked")


@pytest.mark.asyncio
async def test_presidio_sets_guardrail_information_in_request_data():
    """
    Test that Presidio populates guardrail information into request_data metadata.

    This validates that add_standard_logging_guardrail_information_to_request_data
    correctly sets the guardrail information that will be used for logging.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        guardrail_name="test_presidio",
        output_parse_pii=True,
        mock_testing=True,
    )

    request_data = {
        "messages": [{"role": "user", "content": "Test"}],
        "model": "gpt-4o",
        "metadata": {},
    }

    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        assert request_data is not None

        presidio.add_standard_logging_guardrail_information_to_request_data(
            guardrail_provider="presidio",
            guardrail_json_response=[],
            request_data=request_data,
            guardrail_status="success",
            start_time=1234567890.0,
            end_time=1234567891.0,
            duration=1.0,
            masked_entity_count={"EMAIL_ADDRESS": 1, "PERSON": 1},
        )

        return text

    with patch.object(presidio, "check_pii", mock_check_pii):
        await presidio.apply_guardrail(
            inputs={"texts": ["Test message"]},
            request_data=request_data,
            input_type="request",
        )

    assert "metadata" in request_data
    assert "standard_logging_guardrail_information" in request_data["metadata"]

    guardrail_info_list = request_data["metadata"][
        "standard_logging_guardrail_information"
    ]
    assert isinstance(guardrail_info_list, list)
    assert len(guardrail_info_list) > 0

    guardrail_info = guardrail_info_list[0]
    assert "masked_entity_count" in guardrail_info
    assert guardrail_info["masked_entity_count"]["EMAIL_ADDRESS"] == 1
    assert guardrail_info["masked_entity_count"]["PERSON"] == 1

    print("✓ Presidio sets guardrail_information in request_data")


@pytest.mark.asyncio
async def test_request_data_flows_to_apply_guardrail():
    """
    Test that request_data is correctly passed to apply_guardrail method.

    This validates the fix where guardrail translation handler passes data
    as request_data to apply_guardrail so guardrails can store metadata for logging.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        guardrail_name="test_presidio",
        output_parse_pii=True,
        mock_testing=True,
    )

    request_data = {
        "messages": [{"role": "user", "content": "Test message"}],
        "model": "gpt-4o",
        "metadata": {},
    }

    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        assert request_data is not None, "request_data should be passed to check_pii"
        assert "metadata" in request_data, "request_data should have metadata"

        request_data.setdefault("metadata", {})
        request_data["metadata"]["test_flag"] = "passed_correctly"

        return text

    with patch.object(presidio, "check_pii", mock_check_pii):
        await presidio.apply_guardrail(
            inputs={"texts": ["Test message"]},
            request_data=request_data,
            input_type="request",
        )

        assert "metadata" in request_data
        assert request_data["metadata"].get("test_flag") == "passed_correctly"

    print("✓ request_data correctly passed to apply_guardrail")


@pytest.mark.asyncio
async def test_output_masking_apply_to_output_only(mock_user_api_key):
    """
    Ensure output masking runs when apply_to_output is enabled.
    """

    presidio = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        apply_to_output=True,
        pii_entities_config={PiiEntityType.CREDIT_CARD: PiiAction.MASK},
    )

    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        return text.replace("4111-1111-1111-1111", "[CREDIT_CARD]")

    presidio.check_pii = mock_check_pii

    response = ModelResponse(
        id="1",
        object="chat.completion",
        created=0,
        model="gpt-test",
        choices=[
            Choices(
                message=Message(
                    role="assistant",
                    content="Card is 4111-1111-1111-1111",
                ),
                index=0,
                finish_reason="stop",
            )
        ],
    )

    result = await presidio.async_post_call_success_hook(
        data={},
        user_api_key_dict=mock_user_api_key,
        response=response,
    )

    assert "[CREDIT_CARD]" in result.choices[0].message.content
    assert "4111-1111-1111-1111" not in result.choices[0].message.content


@pytest.mark.asyncio
async def test_presidio_filter_scope_initializer(monkeypatch):
    """
    Ensure initializer respects presidio_filter_scope for input/output/both.
    """

    created = []

    class DummyGuardrail:
        def __init__(self, apply_to_output: bool = False, event_hook=None, **kwargs):
            self.apply_to_output = apply_to_output
            self.event_hook = event_hook
            created.append(self)

        def update_in_memory_litellm_params(self, litellm_params):
            pass

    class DummyManager:
        def __init__(self):
            self.added = []

        def add_litellm_callback(self, cb):
            self.added.append(cb)

    mgr = DummyManager()
    monkeypatch.setattr(litellm, "logging_callback_manager", mgr, raising=False)
    import litellm.proxy.guardrails.guardrail_hooks.presidio as presidio_mod
    import litellm.proxy.guardrails.guardrail_initializers as gi

    monkeypatch.setattr(
        presidio_mod, "_OPTIONAL_PresidioPIIMasking", DummyGuardrail, raising=False
    )
    monkeypatch.setattr(
        gi, "_OPTIONAL_PresidioPIIMasking", DummyGuardrail, raising=False
    )

    # input-only
    created.clear()
    from litellm.proxy.guardrails.guardrail_initializers import initialize_presidio

    params_input = LitellmParams(
        guardrail="presidio", mode="pre_call", presidio_filter_scope="input"
    )
    guardrail_dict = {"guardrail_name": "g1"}
    cb = initialize_presidio(params_input, guardrail_dict)
    assert cb is created[0]
    assert created[0].apply_to_output is False

    # output-only
    created.clear()
    params_output = LitellmParams(
        guardrail="presidio", mode="pre_call", presidio_filter_scope="output"
    )
    cb = initialize_presidio(params_output, guardrail_dict)
    assert len(created) == 1
    assert created[0].apply_to_output is True

    # both -> expect two callbacks (input + output)
    created.clear()
    params_both = LitellmParams(
        guardrail="presidio", mode="pre_call", presidio_filter_scope="both"
    )
    cb = initialize_presidio(params_both, guardrail_dict)
    assert len(created) == 2
    assert any(not c.apply_to_output for c in created)
    assert any(c.apply_to_output for c in created)


def test_initialize_presidio_forwards_rulebook_params(monkeypatch):
    """Ключи рулбука и кэша обязаны доезжать из config.yaml до конструктора.

    Прямая сборка класса в остальных тестах эту связку не проверяет: пока
    initialize_presidio их не пробрасывал, детерминированный слой молча не работал
    в развёрнутом шлюзе, хотя все юнит-тесты проходили.
    """
    created = []

    class DummyGuardrail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.apply_to_output = kwargs.get("apply_to_output", False)
            created.append(self)

    class DummyManager:
        def add_litellm_callback(self, cb):
            pass

    monkeypatch.setattr(litellm, "logging_callback_manager", DummyManager(), raising=False)
    import litellm.proxy.guardrails.guardrail_hooks.presidio as presidio_mod
    import litellm.proxy.guardrails.guardrail_initializers as gi

    # initialize_presidio импортирует класс внутри функции, поэтому патчить надо и модуль-источник.
    monkeypatch.setattr(
        presidio_mod, "_OPTIONAL_PresidioPIIMasking", DummyGuardrail, raising=False
    )
    monkeypatch.setattr(gi, "_OPTIONAL_PresidioPIIMasking", DummyGuardrail, raising=False)

    params = LitellmParams(
        guardrail="presidio",
        mode="pre_call",
        presidio_filter_scope="input",
        pii_rulebook="/etc/litellm/pii/rulebook.yaml",
        pii_rule_groups=["personal_data", "names"],
        span_cache_size=1234,
        require_person_entity=True,
    )
    gi.initialize_presidio(params, {"guardrail_name": "g1"})

    kwargs = created[0].kwargs
    assert kwargs["pii_rulebook"] == "/etc/litellm/pii/rulebook.yaml"
    assert kwargs["pii_rule_groups"] == ["personal_data", "names"]
    assert kwargs["span_cache_size"] == 1234
    assert kwargs["require_person_entity"] is True


def test_initialize_presidio_keeps_constructor_defaults(monkeypatch):
    """Отсутствие ключа в конфиге не должно затирать дефолт конструктора значением None."""
    created = []

    class DummyGuardrail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.apply_to_output = kwargs.get("apply_to_output", False)
            created.append(self)

    class DummyManager:
        def add_litellm_callback(self, cb):
            pass

    monkeypatch.setattr(litellm, "logging_callback_manager", DummyManager(), raising=False)
    import litellm.proxy.guardrails.guardrail_hooks.presidio as presidio_mod
    import litellm.proxy.guardrails.guardrail_initializers as gi

    # initialize_presidio импортирует класс внутри функции, поэтому патчить надо и модуль-источник.
    monkeypatch.setattr(
        presidio_mod, "_OPTIONAL_PresidioPIIMasking", DummyGuardrail, raising=False
    )
    monkeypatch.setattr(gi, "_OPTIONAL_PresidioPIIMasking", DummyGuardrail, raising=False)

    params = LitellmParams(
        guardrail="presidio", mode="pre_call", presidio_filter_scope="input"
    )
    gi.initialize_presidio(params, {"guardrail_name": "g1"})

    kwargs = created[0].kwargs
    assert "span_cache_size" not in kwargs
    assert "require_person_entity" not in kwargs
    assert kwargs["pii_rulebook"] is None


@pytest.mark.asyncio
async def test_empty_content_handling(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test that Presidio handles empty content gracefully.

    This is common in tool/function calling where assistant messages have
    empty content but include tool_calls.

    Bug fix: Previously crashed with:
    TypeError: argument after ** must be a mapping, not str
    """
    test_data = {
        "messages": [
            {"role": "user", "content": "What is 2+2?"},
            {
                "role": "assistant",
                "content": "",  # Empty content - common in tool calls
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "calculator",
                            "arguments": '{"a":2,"b":2}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_123", "content": "4"},
        ],
        "model": "gpt-4",
    }

    # Mock check_pii to simulate PII processing without needing Presidio API
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        # Empty text returns as-is (this is what our fix ensures)
        return text

    presidio_guardrail.check_pii = mock_check_pii

    # This should not raise an exception
    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    assert result is not None
    assert "messages" in result
    # Verify messages are preserved
    assert len(result["messages"]) == 3

    print("✓ Empty content handling test passed")


@pytest.mark.asyncio
async def test_whitespace_only_content(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test that Presidio handles whitespace-only content gracefully.

    Whitespace-only content should be treated the same as empty content.
    """
    test_data = {
        "messages": [
            {"role": "user", "content": "   "},  # Whitespace only
            {"role": "assistant", "content": "\n\t  "},  # Tabs and newlines
            {"role": "user", "content": "Real question here"},
        ],
        "model": "gpt-4",
    }

    # Mock check_pii to simulate PII processing
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        return text

    presidio_guardrail.check_pii = mock_check_pii

    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    assert result is not None
    assert len(result["messages"]) == 3

    print("✓ Whitespace-only content test passed")


@pytest.mark.asyncio
async def test_analyze_text_with_empty_string():
    """
    Test analyze_text method directly with empty string.

    Should return empty list without making API call to Presidio.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test:5002/",
        presidio_anonymizer_api_base="http://test:5001/",
        output_parse_pii=False,
    )

    # Test with empty string - should return immediately without API call
    result = await presidio.analyze_text(
        text="",
        presidio_config=None,
        request_data={},
    )
    assert result == [], "Empty text should return empty list"

    # Test with whitespace only - should return immediately
    result = await presidio.analyze_text(
        text="   \n\t   ",
        presidio_config=None,
        request_data={},
    )
    assert result == [], "Whitespace-only text should return empty list"

    print("✓ analyze_text empty string test passed")


@pytest.mark.asyncio
async def test_analyze_text_error_dict_handling():
    """
    Test that analyze_text handles error dict responses from Presidio API.

    When Presidio returns {'error': 'No text provided'}, should handle gracefully
    instead of crashing with TypeError.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://mock-presidio:5002/",
        presidio_anonymizer_api_base="http://mock-presidio:5001/",
        output_parse_pii=False,
    )

    with patch.object(
        presidio,
        "_get_session_iterator",
        _make_mock_session_iterator({"error": "No text provided"}),
    ):
        result = await presidio.analyze_text(
            text="some text",
            presidio_config=None,
            request_data={},
        )
    assert result == [], "Error dict should be handled gracefully"

    print("✓ analyze_text error dict handling test passed")


@pytest.mark.asyncio
async def test_analyze_text_string_response_handling():
    """
    Test that analyze_text handles string responses from Presidio API.

    When Presidio returns a string (e.g. error message from websearch/hosted models),
    should handle gracefully instead of crashing with TypeError about mapping vs str.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://mock-presidio:5002/",
        presidio_anonymizer_api_base="http://mock-presidio:5001/",
        output_parse_pii=False,
    )

    with patch.object(
        presidio,
        "_get_session_iterator",
        _make_mock_session_iterator("Internal Server Error"),
    ):
        result = await presidio.analyze_text(
            text="some text",
            presidio_config=None,
            request_data={},
        )
    assert result == [], "String response should be handled gracefully"


@pytest.mark.asyncio
async def test_analyze_text_invalid_response_raises_when_block_configured():
    """
    When pii_entities_config has BLOCK and Presidio returns invalid response,
    should raise GuardrailRaisedException (fail-closed) rather than silently allowing content.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://mock-presidio:5002/",
        presidio_anonymizer_api_base="http://mock-presidio:5001/",
        output_parse_pii=False,
        pii_entities_config={PiiEntityType.CREDIT_CARD: PiiAction.BLOCK},
    )

    with patch.object(
        presidio,
        "_get_session_iterator",
        _make_mock_session_iterator("Internal Server Error"),
    ):
        with pytest.raises(GuardrailRaisedException) as exc_info:
            await presidio.analyze_text(
                text="some text",
                presidio_config=None,
                request_data={},
            )
    assert "BLOCK" in str(exc_info.value) or "Presidio" in str(exc_info.value)


@pytest.mark.asyncio
async def test_analyze_text_invalid_response_raises_when_mask_configured():
    """
    When pii_entities_config has MASK and Presidio returns invalid response,
    should raise GuardrailRaisedException (fail-closed) because PII masking is expected.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://mock-presidio:5002/",
        presidio_anonymizer_api_base="http://mock-presidio:5001/",
        output_parse_pii=False,
        pii_entities_config={PiiEntityType.CREDIT_CARD: PiiAction.MASK},
    )

    with patch.object(
        presidio,
        "_get_session_iterator",
        _make_mock_session_iterator("Internal Server Error"),
    ):
        with pytest.raises(GuardrailRaisedException) as exc_info:
            await presidio.analyze_text(
                text="some text",
                presidio_config=None,
                request_data={},
            )
    assert "PII protection is configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_analyze_text_list_with_non_dict_items():
    """
    Test that analyze_text skips non-dict items in the result list.

    When Presidio returns a list containing strings (malformed response),
    should skip invalid items and return parsed valid ones.
    """
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://mock-presidio:5002/",
        presidio_anonymizer_api_base="http://mock-presidio:5001/",
        output_parse_pii=False,
    )

    json_response = [
        {"entity_type": "PERSON", "start": 0, "end": 5, "score": 0.9},
        "invalid_string_item",
        {"entity_type": "EMAIL", "start": 10, "end": 25, "score": 0.85},
    ]
    with patch.object(
        presidio, "_get_session_iterator", _make_mock_session_iterator(json_response)
    ):
        result = await presidio.analyze_text(
            text="some text",
            presidio_config=None,
            request_data={},
        )
    assert len(result) == 2, "Should parse 2 valid dict items and skip the string"
    assert result[0].get("entity_type") == "PERSON"
    assert result[1].get("entity_type") == "EMAIL"


@pytest.mark.asyncio
async def test_tool_calling_complete_scenario(
    presidio_guardrail, mock_user_api_key, mock_cache
):
    """
    Test complete tool calling scenario with PII in user message.

    This tests the real-world scenario where:
    1. User provides a query with PII
    2. Assistant responds with empty content + tool_calls
    3. Tool provides response
    4. Assistant provides final answer
    """
    test_data = {
        "messages": [
            {
                "role": "user",
                "content": "My email is john.doe@example.com. Can you look up my account?",
            },
            {
                "role": "assistant",
                "content": "",  # Empty - tool call
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "lookup_account", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_abc", "content": "Account found"},
            {"role": "assistant", "content": "I found your account information."},
        ],
        "model": "gpt-4",
    }

    # Mock check_pii to simulate PII masking
    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        if "john.doe@example.com" in text:
            return text.replace("john.doe@example.com", "[EMAIL]")
        return text

    presidio_guardrail.check_pii = mock_check_pii

    result = await presidio_guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    assert result is not None
    # Verify PII was masked in user message
    assert "[EMAIL]" in result["messages"][0]["content"]
    assert "john.doe@example.com" not in result["messages"][0]["content"]
    # Verify other messages preserved
    assert len(result["messages"]) == 4

    print("✓ Tool calling complete scenario test passed")


def test_filter_drops_low_score_detection():
    """
    Detections below the configured score threshold should be removed.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_score_thresholds={PiiEntityType.CREDIT_CARD: 0.8},
    )
    analyze_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.7, "start": 0, "end": 4}
    ]

    filtered = guardrail.filter_analyze_results_by_score(analyze_results)
    assert filtered == []


def test_filter_preserves_high_score_detection():
    """
    Detections meeting the score threshold should be preserved.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_score_thresholds={PiiEntityType.CREDIT_CARD: 0.8},
    )
    analyze_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.9, "start": 0, "end": 4}
    ]

    filtered = guardrail.filter_analyze_results_by_score(analyze_results)
    assert len(filtered) == 1
    assert filtered[0]["entity_type"] == PiiEntityType.CREDIT_CARD


def test_no_thresholds_returns_all():
    """
    With no thresholds configured, all detections are kept.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(mock_testing=True)
    analyze_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.1, "start": 0, "end": 4},
        {
            "entity_type": PiiEntityType.EMAIL_ADDRESS,
            "score": 0.2,
            "start": 5,
            "end": 9,
        },
    ]

    filtered = guardrail.filter_analyze_results_by_score(analyze_results)
    assert len(filtered) == 2


def test_entity_specific_threshold_only_applies_to_that_entity():
    """
    Entity-specific thresholds do not affect other entity types.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_score_thresholds={PiiEntityType.CREDIT_CARD: 0.8},
    )
    analyze_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.7, "start": 0, "end": 4},
        {
            "entity_type": PiiEntityType.EMAIL_ADDRESS,
            "score": 0.1,
            "start": 5,
            "end": 9,
        },
    ]

    filtered = guardrail.filter_analyze_results_by_score(analyze_results)
    # CREDIT_CARD is filtered, EMAIL_ADDRESS is kept because no threshold
    assert len(filtered) == 1
    assert filtered[0]["entity_type"] == PiiEntityType.EMAIL_ADDRESS


def test_filter_uses_default_all_threshold():
    """
    Default ALL threshold applies to any entity without a specific override.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_score_thresholds={"ALL": 0.75},
    )
    analyze_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.7, "start": 0, "end": 4},
        {
            "entity_type": PiiEntityType.EMAIL_ADDRESS,
            "score": 0.8,
            "start": 5,
            "end": 9,
        },
    ]

    filtered = guardrail.filter_analyze_results_by_score(analyze_results)
    assert len(filtered) == 1
    assert filtered[0]["entity_type"] == PiiEntityType.EMAIL_ADDRESS


def test_entity_specific_overrides_default_threshold():
    """
    Entity-specific threshold should override the ALL default.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_score_thresholds={
            "ALL": 0.8,
            PiiEntityType.CREDIT_CARD: 0.6,
        },
    )
    analyze_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.65, "start": 0, "end": 4},
        {
            "entity_type": PiiEntityType.EMAIL_ADDRESS,
            "score": 0.75,
            "start": 5,
            "end": 9,
        },
    ]

    filtered = guardrail.filter_analyze_results_by_score(analyze_results)
    # CREDIT_CARD passes due to override, EMAIL_ADDRESS dropped by ALL threshold
    assert len(filtered) == 1
    assert filtered[0]["entity_type"] == PiiEntityType.CREDIT_CARD


@pytest.mark.asyncio
async def test_anonymize_skips_when_no_detections_after_filter():
    """
    When all detections are filtered out, anonymize_text should return the original text.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_score_thresholds={PiiEntityType.CREDIT_CARD: 0.8},
    )
    masked_entity_count = {}
    text = "4111"

    filtered = guardrail.filter_analyze_results_by_score(
        [{"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.7, "start": 0, "end": 4}]
    )

    result = await guardrail.anonymize_text(
        text=text,
        analyze_results=filtered,
        output_parse_pii=False,
        masked_entity_count=masked_entity_count,
    )

    assert result == text
    assert masked_entity_count == {}


def test_blocking_respects_threshold_filter():
    """
    Entities filtered out by score should not trigger blocking, but high-score detections should.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        pii_entities_config={PiiEntityType.CREDIT_CARD: PiiAction.BLOCK},
        presidio_score_thresholds={PiiEntityType.CREDIT_CARD: 0.9},
    )

    low_score_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.7, "start": 0, "end": 4}
    ]
    filtered = guardrail.filter_analyze_results_by_score(low_score_results)
    guardrail.raise_exception_if_blocked_entities_detected(filtered)

    high_score_results = [
        {"entity_type": PiiEntityType.CREDIT_CARD, "score": 0.95, "start": 0, "end": 4}
    ]
    filtered_high = guardrail.filter_analyze_results_by_score(high_score_results)
    with pytest.raises(Exception):
        guardrail.raise_exception_if_blocked_entities_detected(filtered_high)


def test_update_in_memory_applies_score_thresholds():
    """
    update_in_memory_litellm_params should refresh score thresholds.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(mock_testing=True)
    assert guardrail.presidio_score_thresholds == {}

    params = LitellmParams(
        guardrail="presidio",
        mode="pre_call",
        presidio_score_thresholds={PiiEntityType.CREDIT_CARD: 0.85},
    )
    guardrail.update_in_memory_litellm_params(params)

    assert guardrail.presidio_score_thresholds == {PiiEntityType.CREDIT_CARD: 0.85}


@pytest.mark.asyncio
async def test_session_iterator_does_not_serialize_callers(presidio_guardrail):
    """Держатели сессии обязаны работать одновременно.

    Пока замок сессии удерживался на всё время запроса, обращения к анализатору шли
    по одному, и ход агента разбирался последовательно вместо одного gather. Тест
    ловит именно это: считает, сколько вызовов держат сессию одновременно.
    """
    in_flight = 0
    peak = 0

    async def hold_session():
        nonlocal in_flight, peak
        async with presidio_guardrail._get_session_iterator():
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1

    await asyncio.gather(*[hold_session() for _ in range(5)])
    assert peak == 5, f"session holders serialized: peak concurrency {peak} of 5"


@pytest.mark.asyncio
async def test_get_session_iterator_thread_safety(presidio_guardrail):
    """
    Test that _get_session_iterator yields:
    1. The shared session when in the main thread.
    2. A loop-bound cached session when in a background thread (reused per loop for efficiency).
    """
    import threading

    import aiohttp

    # 1. Main Thread Case
    # We are in the "main thread" relative to the guardrail initialization
    async with presidio_guardrail._get_session_iterator() as session:
        assert isinstance(session, aiohttp.ClientSession)
        assert session is presidio_guardrail._http_session
        shared_session_id = id(session)

    # 2. Background Thread Case
    # Define a helper function to run in a thread
    def thread_target(loop, result_future):
        async def run_in_loop():
            # This runs in the thread's loop
            async with presidio_guardrail._get_session_iterator() as session:
                return session, id(session)

        try:
            # Create a new loop for this thread to run async code
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            session_obj, session_id = new_loop.run_until_complete(run_in_loop())
            result_future.set_result((session_obj, session_id))
            new_loop.close()
        except Exception as e:
            result_future.set_exception(e)

    # Run the background thread test
    bg_future = asyncio.Future()
    t = threading.Thread(
        target=thread_target, args=(asyncio.get_running_loop(), bg_future)
    )
    t.start()
    t.join()

    bg_session, bg_session_id = await bg_future

    # Assertions
    # The background session should be DIFFERENT from the shared session
    assert bg_session_id != shared_session_id
    # The shared session should still be open (not closed by the background thread)
    assert not presidio_guardrail._http_session.closed
    # The background session should be cached in _loop_sessions and remain open for reuse
    # (Changed behavior: no longer closes immediately, cached per loop for efficiency)
    assert not bg_session.closed, "Background session should remain open for reuse"

    print("✓ Session iterator thread safety test passed")


from litellm.types.utils import ModelResponseStream


@pytest.mark.asyncio
async def test_streaming_with_bytes_chunks_does_not_crash(mock_user_api_key):
    """
    Regression test: async_post_call_streaming_iterator_hook should
    gracefully handle raw bytes in the stream instead of crashing with
    'bytes' object has no attribute 'id'.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        apply_to_output=True,
        mock_redacted_text={"text": "redacted"},
    )

    async def mock_stream():
        yield b'data: {"id":"chatcmpl-1"}\n\n'  # raw bytes
        yield ModelResponseStream(
            id="chatcmpl-1",
            choices=[],
            created=1,
            model="gpt-4",
            object="chat.completion.chunk",
            system_fingerprint=None,
        )  # proper chunk

    chunks = []
    async for chunk in guardrail.async_post_call_streaming_iterator_hook(
        user_api_key_dict=mock_user_api_key,
        response=mock_stream(),
        request_data={},
    ):
        chunks.append(chunk)

    # Should not crash, should produce at least one valid chunk
    assert len(chunks) >= 1


def test_entity_deny_list_filters_detections():
    """
    Verify presidio_entities_deny_list removes matching entity types.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_entities_deny_list=["US_DRIVER_LICENSE"],
    )

    results = [
        {"entity_type": "US_DRIVER_LICENSE", "start": 0, "end": 2, "score": 0.6},
        {"entity_type": "CREDIT_CARD", "start": 10, "end": 26, "score": 0.95},
    ]

    filtered = guardrail.filter_analyze_results_by_score(results)

    assert len(filtered) == 1
    assert filtered[0]["entity_type"] == "CREDIT_CARD"


def test_deny_list_and_score_threshold_combined():
    """
    Verify deny list + score threshold work together correctly.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        presidio_entities_deny_list=["US_DRIVER_LICENSE"],
        presidio_score_thresholds={"ALL": 0.8},
    )

    results = [
        {"entity_type": "US_DRIVER_LICENSE", "start": 0, "end": 2, "score": 0.95},
        {"entity_type": "CREDIT_CARD", "start": 10, "end": 26, "score": 0.6},
        {"entity_type": "EMAIL_ADDRESS", "start": 30, "end": 50, "score": 0.9},
    ]

    filtered = guardrail.filter_analyze_results_by_score(results)

    # US_DRIVER_LICENSE excluded by deny list (even though score > 0.8)
    # CREDIT_CARD excluded by score threshold (0.6 < 0.8)
    # EMAIL_ADDRESS passes both filters
    assert len(filtered) == 1
    assert filtered[0]["entity_type"] == "EMAIL_ADDRESS"


@pytest.mark.asyncio
async def test_analyze_text_non_json_content_type_fail_closed():
    """
    Test that analyze_text raises GuardrailRaisedException when Presidio health
    endpoint returns text/html and fail-closed is enabled.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        pii_entities_config={"PERSON": PiiAction.BLOCK},
        mock_testing=False,
    )

    mock_iterator = _make_mock_session_iterator(
        json_response=None,
        status=200,
        content_type="text/html; charset=utf-8",
        text_response="Presidio Analyzer service is up.",
    )

    with patch.object(guardrail, "_get_session_iterator", mock_iterator):
        with pytest.raises(GuardrailRaisedException) as exc_info:
            await guardrail.analyze_text(
                text="Hello world",
                presidio_config=None,
                request_data={},
            )
        assert "expected application/json Content-Type" in str(exc_info.value)
        assert "text/html" in str(exc_info.value)


@pytest.mark.asyncio
async def test_analyze_text_non_json_content_type_fail_open():
    """
    Test that analyze_text returns empty list when Presidio returns text/html
    and fail-closed is NOT enabled.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        mock_testing=False,
    )

    mock_iterator = _make_mock_session_iterator(
        json_response=None,
        status=200,
        content_type="text/html; charset=utf-8",
        text_response="Presidio Analyzer service is up.",
    )

    with patch.object(guardrail, "_get_session_iterator", mock_iterator):
        results = await guardrail.analyze_text(
            text="Hello world",
            presidio_config=None,
            request_data={},
        )
        assert results == []


@pytest.mark.asyncio
async def test_analyze_text_http_error_status():
    """
    Test that analyze_text handles 5xx HTTP errors properly.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        pii_entities_config={"PERSON": PiiAction.BLOCK},
        mock_testing=False,
    )

    mock_iterator = _make_mock_session_iterator(
        json_response=None,
        status=500,
        content_type="text/plain",
        text_response="Internal Server Error",
    )

    with patch.object(guardrail, "_get_session_iterator", mock_iterator):
        with pytest.raises(GuardrailRaisedException) as exc_info:
            await guardrail.analyze_text(
                text="Hello world",
                presidio_config=None,
                request_data={},
            )
        assert "HTTP 500" in str(exc_info.value)


@pytest.mark.asyncio
async def test_anonymize_text_non_json_content_type():
    """
    Test that anonymize_text raises Exception for non-JSON responses.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        mock_testing=False,
    )

    mock_iterator = _make_mock_session_iterator(
        json_response=None,
        status=200,
        content_type="text/html",
        text_response="Presidio Anonymizer service is up.",
    )

    with patch.object(guardrail, "_get_session_iterator", mock_iterator):
        with pytest.raises(
            Exception, match="Presidio anonymizer returned non-JSON Content-Type"
        ):
            await guardrail.anonymize_text(
                text="Hello world",
                analyze_results=[{"start": 0, "end": 5, "entity_type": "PERSON"}],
                output_parse_pii=False,
                masked_entity_count={},
            )


@pytest.mark.asyncio
async def test_anonymize_text_http_error_status():
    """
    Test that anonymize_text raises Exception on HTTP error.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        mock_testing=False,
    )

    mock_iterator = _make_mock_session_iterator(
        json_response=None,
        status=502,
        content_type="text/plain",
        text_response="Bad Gateway",
    )

    with patch.object(guardrail, "_get_session_iterator", mock_iterator):
        with pytest.raises(Exception, match="Presidio anonymizer returned HTTP 502"):
            await guardrail.anonymize_text(
                text="Hello world",
                analyze_results=[{"start": 0, "end": 5, "entity_type": "PERSON"}],
                output_parse_pii=False,
                masked_entity_count={},
            )


@pytest.mark.asyncio
async def test_pii_tokens_stored_in_metadata_not_top_level(presidio_guardrail):
    """
    Regression test: pii_tokens must be stored in data['metadata']['pii_tokens'],
    NOT in data['pii_tokens']. Storing at the top level leaks the field to LLM
    providers like Anthropic, which reject unknown fields with
    'pii_tokens: Extra inputs are not permitted'.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
        pii_entities_config={
            PiiEntityType.PERSON: PiiAction.MASK,
            PiiEntityType.PHONE_NUMBER: PiiAction.MASK,
        },
    )

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")
    mock_cache = DualCache()

    test_data = {
        "messages": [
            {"role": "user", "content": "My name is John and my phone is 555-123-4567"}
        ],
        "model": "claude-haiku-4-5-20251001",
        "metadata": {},
    }

    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        # Simulate PII masking with token storage (mimics real anonymize_text behavior)
        if request_data is not None and output_parse_pii:
            if "metadata" not in request_data:
                request_data["metadata"] = {}
            if "pii_tokens" not in request_data["metadata"]:
                request_data["metadata"]["pii_tokens"] = {}
            pii_tokens = request_data["metadata"]["pii_tokens"]
            seq = len(pii_tokens) + 1
            token = f"<PERSON_{seq}>"
            pii_tokens[token] = "John"
            text = text.replace("John", token)
        return text

    guardrail.check_pii = mock_check_pii

    result = await guardrail.async_pre_call_hook(
        user_api_key_dict=mock_user_api_key,
        cache=mock_cache,
        data=test_data,
        call_type="completion",
    )

    # pii_tokens must NOT be at the top level of data (would leak to providers)
    assert "pii_tokens" not in result, (
        "pii_tokens must not be a top-level key in request data — "
        "it would leak to LLM providers and cause 'Extra inputs are not permitted' errors"
    )

    # pii_tokens must be inside metadata (safe from provider leakage)
    assert "metadata" in result
    assert "pii_tokens" in result["metadata"]
    assert len(result["metadata"]["pii_tokens"]) > 0


@pytest.mark.asyncio
async def test_pii_tokens_in_metadata_used_for_unmasking():
    """
    Regression test: _process_response_for_pii must read pii_tokens from
    data['metadata']['pii_tokens'] and correctly unmask the response.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    token_key = "<PERSON_1>"
    request_data = {
        "model": "claude-haiku-4-5-20251001",
        "metadata": {"pii_tokens": {token_key: "John"}},
    }

    response = ModelResponse(
        choices=[
            Choices(
                message=Message(
                    role="assistant",
                    content=f"Hello {token_key}, how can I help you?",
                ),
                index=0,
                finish_reason="stop",
            )
        ]
    )

    await guardrail._process_response_for_pii(
        response=response,
        request_data=request_data,
        mode="unmask",
    )

    assert response.choices[0].message.content == "Hello John, how can I help you?"


@pytest.mark.parametrize(
    "initial_hook",
    ["pre_call", "during_call", "pre_mcp_call"],
)
def test_event_hook_auto_expansion_for_all_string_hooks(initial_hook):
    """
    Regression test: when output_parse_pii is True, the guardrail must add
    'post_call' to event_hook regardless of the initial string hook value,
    not just when it's 'pre_call'.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
        event_hook=initial_hook,
    )
    assert isinstance(guardrail.event_hook, list)
    assert initial_hook in guardrail.event_hook
    assert "post_call" in guardrail.event_hook


def test_event_hook_no_expansion_when_already_post_call():
    """post_call alone should stay as-is — no expansion needed."""
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
        event_hook="post_call",
    )
    # Should remain a string "post_call", not expanded to a list
    assert guardrail.event_hook == "post_call"


@pytest.mark.asyncio
async def test_metadata_none_does_not_crash():
    """
    Regression test: if metadata is explicitly None in request_data,
    the guardrail must not crash with TypeError on the write or read path.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    token_key = "<PERSON_1>"
    # metadata explicitly None — must not crash
    request_data = {
        "model": "gpt-3.5-turbo",
        "metadata": None,
    }

    response = ModelResponse(
        choices=[
            Choices(
                message=Message(
                    role="assistant",
                    content=f"Hello {token_key}, how can I help you?",
                ),
                index=0,
                finish_reason="stop",
            )
        ]
    )

    # Should not raise TypeError
    await guardrail._process_response_for_pii(
        response=response,
        request_data=request_data,
        mode="unmask",
    )

    # No pii_tokens to unmask, so content stays as-is
    assert (
        response.choices[0].message.content == f"Hello {token_key}, how can I help you?"
    )


# ---------------------------------------------------------------------------
# Tests for sequential-numbered token unmasking in _unmask_pii_text
# ---------------------------------------------------------------------------


def test_unmask_exact_match_with_sequential_tokens():
    """
    Normal unmasking: LLM echoes numbered tokens verbatim → original PII restored.
    """
    from litellm.proxy.guardrails.guardrail_hooks.presidio import (
        _OPTIONAL_PresidioPIIMasking,
    )

    pii_tokens = {
        "<PERSON_1>": "John Smith",
        "<PHONE_NUMBER_1>": "555-123-4567",
    }
    text = "Hello <PERSON_1>, your number is <PHONE_NUMBER_1>."
    result = _OPTIONAL_PresidioPIIMasking._unmask_pii_text(text, pii_tokens)
    assert result == "Hello John Smith, your number is 555-123-4567."


def test_unmask_multiple_same_entity_type():
    """
    Two phone numbers get distinct numbered tokens and unmask correctly.
    """
    from litellm.proxy.guardrails.guardrail_hooks.presidio import (
        _OPTIONAL_PresidioPIIMasking,
    )

    pii_tokens = {
        "<PHONE_NUMBER_1>": "555-111-0000",
        "<PHONE_NUMBER_2>": "555-222-0000",
    }
    text = "Call <PHONE_NUMBER_1> or <PHONE_NUMBER_2>."
    result = _OPTIONAL_PresidioPIIMasking._unmask_pii_text(text, pii_tokens)
    assert result == "Call 555-111-0000 or 555-222-0000."


def test_unmask_graceful_degradation():
    """
    If the LLM doesn't echo the token back, the numbered label stays
    in the output — clean and readable, not garbage hex.
    """
    from litellm.proxy.guardrails.guardrail_hooks.presidio import (
        _OPTIONAL_PresidioPIIMasking,
    )

    pii_tokens = {
        "<PERSON_1>": "John",
    }
    # LLM paraphrased instead of echoing the token
    text = "I see you provided a name."
    result = _OPTIONAL_PresidioPIIMasking._unmask_pii_text(text, pii_tokens)
    # No change — no garbage, just clean text
    assert result == text


# ---------------------------------------------------------------------------
# Fix 1: Position bug — reverse sort + original text coordinates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymize_text_multiple_items_position_correctness():
    """
    Regression test: when multiple PII items exist, coordinates reference the
    ORIGINAL text. Processing in reverse order prevents coordinate drift.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        mock_testing=False,
    )

    # "Call John at 555-123-4567"
    #   "John" at [5:9], "555-123-4567" at [13:25]
    anonymizer_response = {
        "text": "Call <PERSON> at <PHONE_NUMBER>",
        "items": [
            {
                "start": 5,
                "end": 9,
                "entity_type": "PERSON",
                "text": "<PERSON>",
                "operator": "replace",
            },
            {
                "start": 13,
                "end": 25,
                "entity_type": "PHONE_NUMBER",
                "text": "<PHONE_NUMBER>",
                "operator": "replace",
            },
        ],
    }

    mock_iterator = _make_mock_session_iterator(anonymizer_response)

    request_data = {"metadata": {}}
    with patch.object(guardrail, "_get_session_iterator", mock_iterator):
        result = await guardrail.anonymize_text(
            text="Call John at 555-123-4567",
            analyze_results=[
                {"start": 5, "end": 9, "entity_type": "PERSON", "score": 0.9},
                {"start": 13, "end": 25, "entity_type": "PHONE_NUMBER", "score": 0.95},
            ],
            output_parse_pii=True,
            masked_entity_count={},
            request_data=request_data,
        )

    pii_tokens = request_data["metadata"]["pii_tokens"]

    # Verify tokens captured the correct ORIGINAL text values
    person_token = [k for k in pii_tokens if "PERSON" in k][0]
    phone_token = [k for k in pii_tokens if "PHONE" in k][0]
    assert pii_tokens[person_token] == "John"
    assert pii_tokens[phone_token] == "555-123-4567"

    # Verify both PII values are masked in the result
    assert "John" not in result
    assert "555-123-4567" not in result


# ---------------------------------------------------------------------------
# Fix 2: Anthropic native dict response handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_native_response_unmasking():
    """
    Anthropic native dict responses (type='message') should be unmasked
    when output_parse_pii is enabled.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    request_data = {
        "model": "claude-3-haiku",
        "metadata": {
            "pii_tokens": {
                "<PERSON_1>": "John Smith",
                "<PHONE_NUMBER_1>": "555-123-4567",
            }
        },
    }

    anthropic_response = {
        "type": "message",
        "id": "msg_123",
        "model": "claude-3-haiku",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "Hello <PERSON_1>, your number is <PHONE_NUMBER_1>.",
            }
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")

    result = await guardrail.async_post_call_success_hook(
        data=request_data,
        user_api_key_dict=mock_user_api_key,
        response=anthropic_response,
    )

    assert result["content"][0]["text"] == (
        "Hello John Smith, your number is 555-123-4567."
    )


@pytest.mark.asyncio
async def test_anthropic_native_response_masking():
    """
    Anthropic native dict responses should be masked when
    apply_to_output is enabled.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        apply_to_output=True,
    )

    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        return text.replace("John Smith", "[PERSON]").replace("555-123-4567", "[PHONE]")

    guardrail.check_pii = mock_check_pii

    anthropic_response = {
        "type": "message",
        "id": "msg_123",
        "model": "claude-3-haiku",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello John Smith, call 555-123-4567."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")

    result = await guardrail.async_post_call_success_hook(
        data={},
        user_api_key_dict=mock_user_api_key,
        response=anthropic_response,
    )

    assert "[PERSON]" in result["content"][0]["text"]
    assert "[PHONE]" in result["content"][0]["text"]
    assert "John Smith" not in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_anthropic_native_response_non_text_blocks_untouched():
    """
    Non-text blocks (tool_use, thinking) in Anthropic responses
    should be left untouched during unmasking.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    request_data = {
        "model": "claude-3-haiku",
        "metadata": {"pii_tokens": {"<PERSON_1>": "John"}},
    }

    anthropic_response = {
        "type": "message",
        "id": "msg_123",
        "content": [
            {"type": "text", "text": "Hello <PERSON_1>"},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "search",
                "input": {"q": "test"},
            },
        ],
        "role": "assistant",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")

    result = await guardrail.async_post_call_success_hook(
        data=request_data,
        user_api_key_dict=mock_user_api_key,
        response=anthropic_response,
    )

    assert result["content"][0]["text"] == "Hello John"
    assert result["content"][1]["type"] == "tool_use"
    assert result["content"][1]["name"] == "search"


# ---------------------------------------------------------------------------
# Fix 3: Anthropic native SSE streaming — bytes passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_bytes_chunks_are_yielded_not_discarded():
    """
    Regression test: bytes chunks (Anthropic native SSE) should be yielded
    through the streaming hook, not silently discarded.
    """

    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        apply_to_output=True,
    )

    byte_chunk = b'data: {"type":"content_block_delta","delta":{"text":"Hello"}}\n\n'

    async def mock_stream():
        yield byte_chunk

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")
    chunks = []
    async for chunk in guardrail.async_post_call_streaming_iterator_hook(
        user_api_key_dict=mock_user_api_key,
        response=mock_stream(),
        request_data={},
    ):
        chunks.append(chunk)

    assert any(
        isinstance(c, bytes) for c in chunks
    ), "bytes chunks must not be discarded"
    assert byte_chunk in chunks


@pytest.mark.asyncio
async def test_streaming_unmask_path_bytes_passthrough():
    """
    Bytes chunks in the unmasking path should also pass through.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    byte_chunk = b'data: {"type":"content_block_delta"}\n\n'
    request_data = {
        "metadata": {"pii_tokens": {"<PERSON_1>": "John"}},
    }

    async def mock_stream():
        yield byte_chunk

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")
    chunks = []
    async for chunk in guardrail.async_post_call_streaming_iterator_hook(
        user_api_key_dict=mock_user_api_key,
        response=mock_stream(),
        request_data=request_data,
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0] == byte_chunk


@pytest.mark.asyncio
async def test_apply_to_output_streaming_unknown_events_passthrough():
    """
    Regression test: /v1/responses-style event objects (neither bytes nor
    ModelResponseStream) must be preserved in order and not dropped.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        apply_to_output=True,
    )

    class FakeResponsesEvent:
        def __init__(self, event_type: str):
            self.type = event_type

    events = [
        FakeResponsesEvent("response.created"),
        FakeResponsesEvent("response.output_text.delta"),
        FakeResponsesEvent("response.completed"),
    ]

    async def mock_stream():
        for event in events:
            yield event

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")
    received = []
    async for chunk in guardrail.async_post_call_streaming_iterator_hook(
        user_api_key_dict=mock_user_api_key,
        response=mock_stream(),
        request_data={},
    ):
        received.append(chunk)

    # Preserve exact objects and ordering so clients receive full event lifecycle.
    assert received == events
    assert [e.type for e in received] == [
        "response.created",
        "response.output_text.delta",
        "response.completed",
    ]


@pytest.mark.asyncio
async def test_apply_to_output_streaming_mixed_chunks_flushes_and_warns():
    """
    Regression test for mixed stream shape:
    a buffered ModelResponseStream chunk followed by unknown responses-style
    events should be preserved, and masking skip should be visible via warnings.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        apply_to_output=True,
    )

    class FakeResponsesEvent:
        def __init__(self, event_type: str):
            self.type = event_type

    model_chunk = ModelResponseStream(
        id="chatcmpl-mixed-1",
        choices=[],
        created=1,
        model="gpt-4",
        object="chat.completion.chunk",
        system_fingerprint=None,
    )
    response_completed = FakeResponsesEvent("response.completed")

    async def mock_stream():
        yield model_chunk
        yield response_completed

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")
    received = []
    with patch(
        "litellm.proxy.guardrails.guardrail_hooks.presidio.verbose_proxy_logger"
    ) as mock_logger:
        async for chunk in guardrail.async_post_call_streaming_iterator_hook(
            user_api_key_dict=mock_user_api_key,
            response=mock_stream(),
            request_data={},
        ):
            received.append(chunk)

        # Preserve original ordering across mixed stream types.
        assert received == [model_chunk, response_completed]

        # Two warnings are expected:
        # 1) mixed stream detected + unmasked flush
        # 2) passthrough mode skipped output masking
        assert mock_logger.warning.call_count == 2
        warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
        assert any("mixed stream detected" in msg for msg in warning_messages)
        assert any("unknown event objects" in msg for msg in warning_messages)


# ---------------------------------------------------------------------------
# Fix 4: apply_guardrail unmask path for input_type="response"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_guardrail_unmask_on_response():
    """
    When input_type is 'response' and pii_tokens exist, apply_guardrail
    should unmask text instead of masking it.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        guardrail_name="test_presidio",
        output_parse_pii=True,
        mock_testing=True,
    )

    request_data = {
        "model": "gpt-4o",
        "metadata": {
            "pii_tokens": {
                "<PERSON_1>": "John Smith",
                "<PHONE_NUMBER_1>": "555-123-4567",
            }
        },
    }

    inputs = {
        "texts": [
            "Hello <PERSON_1>, your number is <PHONE_NUMBER_1>.",
        ]
    }

    result = await guardrail.apply_guardrail(
        inputs=inputs,
        request_data=request_data,
        input_type="response",
    )

    assert result["texts"][0] == "Hello John Smith, your number is 555-123-4567."


@pytest.mark.asyncio
async def test_apply_guardrail_masks_on_request():
    """
    When input_type is 'request', apply_guardrail should mask as before.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        guardrail_name="test_presidio",
        output_parse_pii=True,
        mock_testing=True,
    )

    async def mock_check_pii(text, output_parse_pii, presidio_config, request_data):
        return text.replace("John Smith", "<PERSON>")

    guardrail.check_pii = mock_check_pii

    result = await guardrail.apply_guardrail(
        inputs={"texts": ["Hello John Smith"]},
        request_data={"model": "gpt-4o", "metadata": {}},
        input_type="request",
    )

    assert "<PERSON>" in result["texts"][0]
    assert "John Smith" not in result["texts"][0]


@pytest.mark.asyncio
async def test_apply_to_output_streaming_bytes_only_logs_warning():
    """
    Regression test: when apply_to_output=True and the stream contains only
    bytes chunks (Anthropic native SSE), output masking is skipped.
    A warning must be logged so operators are aware.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        apply_to_output=True,
    )

    byte_chunks = [
        b'data: {"type":"content_block_delta","delta":{"text":"Hello"}}\n\n',
        b'data: {"type":"content_block_delta","delta":{"text":" world"}}\n\n',
    ]

    async def mock_stream():
        for b in byte_chunks:
            yield b

    mock_user_api_key = UserAPIKeyAuth(api_key="test-key")

    collected = []
    with patch(
        "litellm.proxy.guardrails.guardrail_hooks.presidio.verbose_proxy_logger"
    ) as mock_logger:
        async for chunk in guardrail.async_post_call_streaming_iterator_hook(
            user_api_key_dict=mock_user_api_key,
            response=mock_stream(),
            request_data={},
        ):
            collected.append(chunk)

        # All bytes should be yielded through
        assert len(collected) == len(byte_chunks)
        for original, received in zip(byte_chunks, collected):
            assert original == received

        # Warning must be logged about skipped masking
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "Output PII masking was skipped" in warning_msg


@pytest.mark.asyncio
async def test_output_parse_pii_streaming_responses_events_passthrough(
    mock_user_api_key,
):
    """
    Regression test: when output_parse_pii=True and pii_tokens exist, /v1/responses
    streaming events must pass through instead of being dropped.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    response_events = [
        {"type": "response.created", "response": {"id": "resp_1"}},
        {"type": "response.output_text.delta", "delta": "Hello"},
        {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed"},
        },
    ]

    async def mock_stream():
        for event in response_events:
            yield event

    collected = []
    async for chunk in guardrail.async_post_call_streaming_iterator_hook(
        user_api_key_dict=mock_user_api_key,
        response=mock_stream(),
        request_data={
            "metadata": {
                "pii_tokens": {"<EMAIL_ADDRESS_1>": "john@example.com"},
            }
        },
    ):
        collected.append(chunk)

    assert collected == response_events


@pytest.mark.asyncio
async def test_output_parse_pii_streaming_responses_completed_event_unmasked(
    mock_user_api_key,
):
    """
    When output_parse_pii=True, a /v1/responses ``response.completed`` event
    (a Pydantic ResponseCompletedEvent, as produced in production) must have its
    output text unmasked in-place before being forwarded to the client.
    """
    from litellm.types.llms.openai import (
        ResponseCompletedEvent,
        ResponsesAPIResponse,
        ResponsesAPIStreamEvents,
    )
    from litellm.types.responses.main import GenericResponseOutputItem, OutputText

    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    completed_event = ResponseCompletedEvent(
        type=ResponsesAPIStreamEvents.RESPONSE_COMPLETED,
        response=ResponsesAPIResponse(
            id="resp_1",
            created_at=1,
            output=[
                GenericResponseOutputItem(
                    type="message",
                    id="msg_1",
                    status="completed",
                    role="assistant",
                    content=[
                        OutputText(
                            type="output_text",
                            text="Reach me at <EMAIL_ADDRESS_1> today.",
                            annotations=[],
                        )
                    ],
                )
            ],
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
        ),
    )

    async def mock_stream():
        yield completed_event

    collected = []
    async for chunk in guardrail.async_post_call_streaming_iterator_hook(
        user_api_key_dict=mock_user_api_key,
        response=mock_stream(),
        request_data={
            "metadata": {
                "pii_tokens": {"<EMAIL_ADDRESS_1>": "john@example.com"},
            }
        },
    ):
        collected.append(chunk)

    assert collected == [completed_event]
    assert (
        collected[0].response.output[0].content[0].text
        == "Reach me at john@example.com today."
    )


@pytest.mark.asyncio
async def test_output_parse_pii_streaming_mixed_chunks_flushes_buffered(
    mock_user_api_key,
):
    """
    Regression test: when output_parse_pii=True and a stream mixes buffered
    ModelResponseStream chunks with a /v1/responses event, the buffered chat
    chunks must still be forwarded (in order) instead of being dropped at the
    saw_non_chat_chunk early return.
    """
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    class FakeResponsesEvent:
        def __init__(self, event_type: str):
            self.type = event_type

    model_chunk = ModelResponseStream(
        id="chatcmpl-mixed-unmask-1",
        choices=[],
        created=1,
        model="gpt-4",
        object="chat.completion.chunk",
        system_fingerprint=None,
    )
    response_completed = FakeResponsesEvent("response.completed")

    async def mock_stream():
        yield model_chunk
        yield response_completed

    collected = []
    async for chunk in guardrail.async_post_call_streaming_iterator_hook(
        user_api_key_dict=mock_user_api_key,
        response=mock_stream(),
        request_data={
            "metadata": {
                "pii_tokens": {"<EMAIL_ADDRESS_1>": "john@example.com"},
            }
        },
    ):
        collected.append(chunk)

    assert collected == [model_chunk, response_completed]


@pytest.mark.asyncio
async def test_anonymize_text_uses_correct_positions_no_parse_pii():
    """
    Regression test for anonymizer offset bug (fixes #24160).

    The Presidio anonymizer returns items with start/end positions that
    reference the *anonymized output* text, not the original input text.
    When output_parse_pii is False, anonymize_text must return
    redacted_text["text"] directly instead of manually splicing the
    original text using those positions, which produces garbled output
    with remnants of original PII data.
    """
    original_text = (
        "My name is John Smith, my email is john@example.com, phone 555-867-5309"
    )
    # Positions as returned by the analyzer (reference original text)
    analyze_results = [
        {"end": 51, "entity_type": "EMAIL_ADDRESS", "score": 1.0, "start": 35},
        {"end": 21, "entity_type": "PERSON", "score": 0.85, "start": 11},
        {"end": 71, "entity_type": "PHONE_NUMBER", "score": 0.75, "start": 59},
    ]
    # Anonymizer response — positions reference the *anonymized* text
    anonymizer_response = {
        "text": "My name is <PERSON>, my email is <EMAIL_ADDRESS>, phone <PHONE_NUMBER>",
        "items": [
            {
                "start": 56,
                "end": 70,
                "entity_type": "PHONE_NUMBER",
                "text": "<PHONE_NUMBER>",
                "operator": "replace",
            },
            {
                "start": 33,
                "end": 48,
                "entity_type": "EMAIL_ADDRESS",
                "text": "<EMAIL_ADDRESS>",
                "operator": "replace",
            },
            {
                "start": 11,
                "end": 19,
                "entity_type": "PERSON",
                "text": "<PERSON>",
                "operator": "replace",
            },
        ],
    }

    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        mock_testing=False,
    )

    mock_iterator = _make_mock_session_iterator(
        json_response=anonymizer_response,
    )

    masked_entity_count = {}
    with patch.object(guardrail, "_get_session_iterator", mock_iterator):
        result = await guardrail.anonymize_text(
            text=original_text,
            analyze_results=analyze_results,
            output_parse_pii=False,
            masked_entity_count=masked_entity_count,
        )

    expected = "My name is <PERSON>, my email is <EMAIL_ADDRESS>, phone <PHONE_NUMBER>"
    assert (
        result == expected
    ), f"anonymize_text produced garbled output with PII remnants.\nExpected: {expected!r}\nGot:      {result!r}"
    assert masked_entity_count == {
        "PERSON": 1,
        "EMAIL_ADDRESS": 1,
        "PHONE_NUMBER": 1,
    }


@pytest.mark.asyncio
async def test_anonymize_text_uses_correct_positions_with_parse_pii():
    """
    Regression test for anonymizer offset bug with output_parse_pii=True
    (fixes #24160).

    When output_parse_pii is True, anonymize_text must use positions from
    analyze_results (which reference the original text) to build numbered
    tokens and the pii_tokens mapping, not positions from anonymizer items
    (which reference the anonymized output text).
    """
    original_text = (
        "My name is John Smith, my email is john@example.com, phone 555-867-5309"
    )
    analyze_results = [
        {"end": 51, "entity_type": "EMAIL_ADDRESS", "score": 1.0, "start": 35},
        {"end": 21, "entity_type": "PERSON", "score": 0.85, "start": 11},
        {"end": 71, "entity_type": "PHONE_NUMBER", "score": 0.75, "start": 59},
    ]
    anonymizer_response = {
        "text": "My name is <PERSON>, my email is <EMAIL_ADDRESS>, phone <PHONE_NUMBER>",
        "items": [
            {
                "start": 56,
                "end": 70,
                "entity_type": "PHONE_NUMBER",
                "text": "<PHONE_NUMBER>",
                "operator": "replace",
            },
            {
                "start": 33,
                "end": 48,
                "entity_type": "EMAIL_ADDRESS",
                "text": "<EMAIL_ADDRESS>",
                "operator": "replace",
            },
            {
                "start": 11,
                "end": 19,
                "entity_type": "PERSON",
                "text": "<PERSON>",
                "operator": "replace",
            },
        ],
    }

    guardrail = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://test-analyzer/",
        presidio_anonymizer_api_base="http://test-anonymizer/",
        mock_testing=False,
        output_parse_pii=True,
    )

    mock_iterator = _make_mock_session_iterator(
        json_response=anonymizer_response,
    )

    masked_entity_count = {}
    request_data = {"metadata": {}}
    anonymizer_call = AsyncMock(return_value=anonymizer_response)
    with patch.object(guardrail, "_get_session_iterator", mock_iterator), patch.object(
        guardrail, "_post_presidio_anonymize", anonymizer_call
    ):
        result = await guardrail.anonymize_text(
            text=original_text,
            analyze_results=analyze_results,
            output_parse_pii=True,
            masked_entity_count=masked_entity_count,
            request_data=request_data,
        )

    # Result must not contain any remnants of original PII
    assert "John" not in result
    assert "john@example.com" not in result
    assert "555-867-5309" not in result

    # pii_tokens must map numbered tokens back to correct original values
    pii_tokens = request_data["metadata"]["pii_tokens"]
    token_values = set(pii_tokens.values())
    assert "John Smith" in token_values
    assert "john@example.com" in token_values
    assert "555-867-5309" in token_values

    # Tokens must be numbered in left-to-right order of appearance:
    # PERSON (pos 11) → _1, EMAIL_ADDRESS (pos 35) → _2, PHONE_NUMBER (pos 59) → _3
    assert pii_tokens.get("<PERSON_1>") == "John Smith"

    # Нумерованные плейсхолдеры строятся локально, поэтому анонимайзер в этой ветке
    # не нужен вовсе. Без этой проверки возврат лишнего round-trip прошёл бы молча:
    # мок выше просто перестал бы использоваться, а тест остался бы зелёным.
    anonymizer_call.assert_not_awaited()
    assert pii_tokens.get("<EMAIL_ADDRESS_2>") == "john@example.com"
    assert pii_tokens.get("<PHONE_NUMBER_3>") == "555-867-5309"


def test_unmask_sse_bytes_chunk_replaces_text_delta():
    import json

    pii_tokens = {"<PERSON_1>": "Bobby"}
    event = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Hello <PERSON_1>, how are you?"},
    }
    chunk = ("data: " + json.dumps(event) + "\n\n").encode("utf-8")

    result = _OPTIONAL_PresidioPIIMasking._unmask_sse_bytes_chunk(chunk, pii_tokens)

    decoded = result.decode("utf-8")
    parsed = json.loads(decoded.split("data: ", 1)[1].strip())
    assert parsed["delta"]["text"] == "Hello Bobby, how are you?"


def test_unmask_sse_bytes_chunk_ignores_non_text_delta():
    import json

    pii_tokens = {"<PERSON_1>": "Bobby"}

    # message_start event — no delta
    event = {"type": "message_start", "message": {"id": "msg_01", "role": "assistant"}}
    chunk = ("data: " + json.dumps(event) + "\n\n").encode("utf-8")
    result = _OPTIONAL_PresidioPIIMasking._unmask_sse_bytes_chunk(chunk, pii_tokens)
    assert result == chunk

    # input_json_delta — should not be touched
    event2 = {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"name": "<PERSON_1>"}'},
    }
    chunk2 = ("data: " + json.dumps(event2) + "\n\n").encode("utf-8")
    result2 = _OPTIONAL_PresidioPIIMasking._unmask_sse_bytes_chunk(chunk2, pii_tokens)
    assert result2 == chunk2


def test_unmask_sse_bytes_chunk_handles_malformed_json():
    chunk = b"data: {not valid json}\n\n"
    result = _OPTIONAL_PresidioPIIMasking._unmask_sse_bytes_chunk(
        chunk, {"<PERSON_1>": "Bobby"}
    )
    assert result == chunk


def test_unmask_sse_bytes_chunk_handles_unicode_decode_error():
    chunk = b"\xff\xfe invalid utf-8"
    result = _OPTIONAL_PresidioPIIMasking._unmask_sse_bytes_chunk(
        chunk, {"<PERSON_1>": "Bobby"}
    )
    assert result == chunk


def test_unmask_sse_bytes_chunk_non_ascii_pii_not_escaped():
    import json

    pii_tokens = {"<PERSON_1>": "José"}
    event = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Hello <PERSON_1>!"},
    }
    chunk = ("data: " + json.dumps(event) + "\n\n").encode("utf-8")

    result = _OPTIONAL_PresidioPIIMasking._unmask_sse_bytes_chunk(chunk, pii_tokens)

    decoded = result.decode("utf-8")
    assert "Jos\\u" not in decoded
    parsed = json.loads(decoded.split("data: ", 1)[1].strip())
    assert parsed["delta"]["text"] == "Hello José!"


def test_unmask_sse_bytes_chunk_handles_crlf_line_endings():
    import json

    pii_tokens = {"<PERSON_1>": "Bobby"}
    event = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Hi <PERSON_1>!"},
    }
    crlf_chunk = ("data: " + json.dumps(event) + "\r\ndata: [DONE]\r\n").encode("utf-8")

    result = _OPTIONAL_PresidioPIIMasking._unmask_sse_bytes_chunk(
        crlf_chunk, pii_tokens
    )

    decoded = result.decode("utf-8")
    parsed = json.loads(decoded.split("data: ", 1)[1].split("\n")[0].strip())
    assert parsed["delta"]["text"] == "Hi Bobby!"
    assert "data: [DONE]" in decoded


@pytest.mark.asyncio
async def test_stream_pii_unmasking_unmaskes_bytes_chunks(mock_user_api_key):
    import json

    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    pii_tokens = {"<PERSON_1>": "Bobby"}
    request_data = {"metadata": {"pii_tokens": pii_tokens}}

    def _make_sse_chunk(text: str) -> bytes:
        event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        }
        return ("data: " + json.dumps(event) + "\n\n").encode("utf-8")

    async def mock_stream():
        yield _make_sse_chunk("Hello <PERSON_1>!")
        yield _make_sse_chunk(" How can I help?")

    chunks = []
    async for chunk in guardrail._stream_pii_unmasking(mock_stream(), request_data):
        chunks.append(chunk)

    assert len(chunks) == 2
    first = chunks[0].decode("utf-8")
    first_event = json.loads(first.split("data: ", 1)[1].strip())
    assert first_event["delta"]["text"] == "Hello Bobby!"

    second = chunks[1].decode("utf-8")
    second_event = json.loads(second.split("data: ", 1)[1].strip())
    assert second_event["delta"]["text"] == " How can I help?"


@pytest.mark.asyncio
async def test_stream_pii_unmasking_passthrough_when_no_tokens(mock_user_api_key):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
    )

    raw_chunk = b"data: {}\n\n"
    request_data: dict = {"metadata": {}}

    async def mock_stream():
        yield raw_chunk

    chunks = []
    async for chunk in guardrail._stream_pii_unmasking(mock_stream(), request_data):
        chunks.append(chunk)

    assert chunks == [raw_chunk]


def test_drop_overlapping_results_keeps_highest_score():
    """Overlapping spans for one substring must collapse to the highest-score entity."""
    results = [
        {"entity_type": "RU_INN", "start": 5, "end": 15, "score": 0.7},
        {"entity_type": "US_BANK_NUMBER", "start": 5, "end": 15, "score": 0.05},
        {"entity_type": "EMAIL_ADDRESS", "start": 20, "end": 30, "score": 1.0},
        {"entity_type": "URL", "start": 22, "end": 30, "score": 0.5},
        {"entity_type": "PERSON", "start": 40, "end": 50, "score": 0.85},
    ]
    kept = _OPTIONAL_PresidioPIIMasking._drop_overlapping_results(results)
    assert sorted(r["entity_type"] for r in kept) == [
        "EMAIL_ADDRESS",
        "PERSON",
        "RU_INN",
    ]


def test_numbered_tokens_do_not_corrupt_on_overlap(presidio_guardrail):
    """Regression: overlapping detections used to stomp each other and mangle the text."""
    text = "call 1234567890 now"
    analyze_results = [
        {"entity_type": "RU_INN", "start": 5, "end": 15, "score": 0.7},
        {"entity_type": "US_BANK_NUMBER", "start": 5, "end": 15, "score": 0.05},
        {"entity_type": "US_DRIVER_LICENSE", "start": 5, "end": 15, "score": 0.01},
    ]
    request_data: dict = {"metadata": {}}
    out = presidio_guardrail._finalize_presidio_anonymize_numbered_tokens(
        text, analyze_results, request_data, {}
    )
    assert out == "call <RU_INN_1> now"
    assert request_data["metadata"]["pii_tokens"] == {"<RU_INN_1>": "1234567890"}


def test_output_unmask_false_skips_post_call_hook():
    """presidio_output_unmask=False keeps numbered placeholders, so no post_call pass."""
    redact = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
        presidio_output_unmask=False,
        pii_entities_config={PiiEntityType.PERSON: PiiAction.MASK},
        event_hook="pre_call",
    )
    redact_hooks = (
        redact.event_hook
        if isinstance(redact.event_hook, list)
        else [redact.event_hook]
    )
    assert "post_call" not in redact_hooks

    restore = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
        presidio_output_unmask=True,
        pii_entities_config={PiiEntityType.PERSON: PiiAction.MASK},
        event_hook="pre_call",
    )
    restore_hooks = (
        restore.event_hook
        if isinstance(restore.event_hook, list)
        else [restore.event_hook]
    )
    assert "post_call" in restore_hooks


@pytest.mark.asyncio
async def test_post_call_unmask_false_keeps_placeholders(mock_user_api_key):
    redact = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
        presidio_output_unmask=False,
        pii_entities_config={PiiEntityType.PERSON: PiiAction.MASK},
        event_hook="pre_call",
    )
    response = ModelResponse(
        choices=[Choices(message=Message(content="<PERSON_1> said hi"))]
    )
    data = {"metadata": {"pii_tokens": {"<PERSON_1>": "Ivan"}}}
    out = await redact.async_post_call_success_hook(
        data=data, user_api_key_dict=mock_user_api_key, response=response
    )
    assert out.choices[0].message.content == "<PERSON_1> said hi"


@pytest.mark.asyncio
async def test_post_call_unmask_true_restores_values(mock_user_api_key):
    restore = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        output_parse_pii=True,
        presidio_output_unmask=True,
        pii_entities_config={PiiEntityType.PERSON: PiiAction.MASK},
        event_hook="pre_call",
    )
    response = ModelResponse(
        choices=[Choices(message=Message(content="<PERSON_1> said hi"))]
    )
    data = {"metadata": {"pii_tokens": {"<PERSON_1>": "Ivan"}}}
    out = await restore.async_post_call_success_hook(
        data=data, user_api_key_dict=mock_user_api_key, response=response
    )
    assert out.choices[0].message.content == "Ivan said hi"


# PiiAction.KEEP: keep spans are never masked/blocked and shadow lower/equal-score overlaps, so a DATE_TIME match keeps a date the greedy PhoneRecognizer would mask (windbit/issues#507).


def _keep_res(entity_type, start, end, score):
    return {"entity_type": entity_type, "start": start, "end": end, "score": score}


def _keep_guardrail(config):
    return _OPTIONAL_PresidioPIIMasking(mock_testing=True, pii_entities_config=config)


def test_keep_drops_keep_span_and_shadowed_overlap():
    g = _keep_guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.MASK}
    )
    out = g.apply_keep_entities(
        [_keep_res("DATE_TIME", 0, 10, 0.60), _keep_res("PHONE_NUMBER", 0, 10, 0.40)]
    )
    assert out == []  # date left untouched: nothing to mask


def test_keep_preserves_nonoverlapping_real_phone():
    g = _keep_guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.MASK}
    )
    real_phone = _keep_res("PHONE_NUMBER", 20, 32, 0.60)
    out = g.apply_keep_entities(
        [_keep_res("DATE_TIME", 0, 10, 0.60), _keep_res("PHONE_NUMBER", 0, 10, 0.40), real_phone]
    )
    assert out == [real_phone]  # a genuine phone elsewhere is still masked


def test_keep_does_not_shadow_higher_score_overlap():
    g = _keep_guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PERSON: PiiAction.MASK}
    )
    person = _keep_res("PERSON", 0, 10, 0.85)
    out = g.apply_keep_entities([_keep_res("DATE_TIME", 0, 10, 0.60), person])
    assert out == [person]


def test_keep_noop_without_keep_config():
    g = _keep_guardrail({PiiEntityType.PHONE_NUMBER: PiiAction.MASK})
    results = [_keep_res("PHONE_NUMBER", 0, 12, 0.60)]
    assert g.apply_keep_entities(results) == results


def test_keep_shadows_block_so_date_is_not_blocked():
    g = _keep_guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.BLOCK}
    )
    date_results = g.apply_keep_entities(
        [_keep_res("DATE_TIME", 0, 10, 0.60), _keep_res("PHONE_NUMBER", 0, 10, 0.40)]
    )
    g.raise_exception_if_blocked_entities_detected(date_results)  # must not raise


def test_keep_still_blocks_real_phone():
    g = _keep_guardrail(
        {PiiEntityType.DATE_TIME: PiiAction.KEEP, PiiEntityType.PHONE_NUMBER: PiiAction.BLOCK}
    )
    phone_results = g.apply_keep_entities([_keep_res("PHONE_NUMBER", 8, 20, 0.60)])
    with pytest.raises(BlockedPiiEntityError):
        g.raise_exception_if_blocked_entities_detected(phone_results)


@pytest.mark.asyncio
async def test_check_pii_keep_lets_date_pass_through():
    """Full check_pii path: a date matched as both DATE_TIME (keep) and PHONE_NUMBER (mask) passes through untouched."""
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://mock-presidio:5002/",
        presidio_anonymizer_api_base="http://mock-presidio:5001/",
        output_parse_pii=True,
        pii_entities_config={
            PiiEntityType.DATE_TIME: PiiAction.KEEP,
            PiiEntityType.PHONE_NUMBER: PiiAction.MASK,
        },
    )
    text = "дата 07.08.2026"  # "07.08.2026" spans indices 5..15
    analyze_json = [
        _keep_res("DATE_TIME", 5, 15, 0.60),
        _keep_res("PHONE_NUMBER", 5, 15, 0.40),
    ]
    with patch.object(
        presidio, "_get_session_iterator", _make_mock_session_iterator(analyze_json)
    ):
        result = await presidio.check_pii(
            text=text,
            output_parse_pii=True,
            presidio_config=None,
            request_data={"metadata": {}},
        )
    assert result == text


@pytest.mark.asyncio
async def test_check_pii_keep_still_masks_real_phone():
    """Full check_pii path: a genuine phone (no overlapping keep span) is masked."""
    presidio = _OPTIONAL_PresidioPIIMasking(
        presidio_analyzer_api_base="http://mock-presidio:5002/",
        presidio_anonymizer_api_base="http://mock-presidio:5001/",
        output_parse_pii=True,
        pii_entities_config={
            PiiEntityType.DATE_TIME: PiiAction.KEEP,
            PiiEntityType.PHONE_NUMBER: PiiAction.MASK,
        },
    )
    text = "звоните 79161234567"  # phone spans indices 8..19
    analyze_json = [_keep_res("PHONE_NUMBER", 8, 19, 0.40)]
    with patch.object(
        presidio, "_get_session_iterator", _make_mock_session_iterator(analyze_json)
    ):
        result = await presidio.check_pii(
            text=text,
            output_parse_pii=True,
            presidio_config=None,
            request_data={"metadata": {}},
        )
    assert "79161234567" not in result
    assert "<PHONE_NUMBER_1>" in result


RULEBOOK_YAML = """
version: wiring-test
groups:
  - name: personal_data
    rules:
      - rule_id: pii.inn.person
        entity: RU_INN
        regex: '\\b\\d{12}\\b'
        validator: inn
  - name: secrets
    rules:
      - rule_id: secrets.aws
        entity: SECRET
        regex: '\\bAKIA[0-9A-Z]{16}\\b'
"""


@pytest.fixture
def rulebook_file(tmp_path):
    path = tmp_path / "rulebook.yaml"
    path.write_text(RULEBOOK_YAML, encoding="utf-8")
    return str(path)


def test_broken_rulebook_fails_startup(tmp_path):
    # Гардрейл с половиной правил тише и опаснее гардрейла, который не поднялся.
    path = tmp_path / "broken.yaml"
    path.write_text(
        "groups:\n  - name: g\n    rules:\n      - rule_id: r\n        entity: E\n"
        "        regex: 'x'\n        validator: nosuch\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="unknown validator"):
        _OPTIONAL_PresidioPIIMasking(mock_testing=True, pii_rulebook=str(path))


def test_missing_rulebook_fails_startup(tmp_path):
    with pytest.raises(Exception, match="cannot read rulebook"):
        _OPTIONAL_PresidioPIIMasking(
            mock_testing=True, pii_rulebook=str(tmp_path / "nope.yaml")
        )


def test_no_rulebook_keeps_engine_off():
    guardrail = _OPTIONAL_PresidioPIIMasking(mock_testing=True)
    assert guardrail.rule_engine is None
    assert guardrail._analyze_with_rules("ИНН 500100732259") == []


def test_rules_find_structured_data_without_the_analyzer(rulebook_file):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file
    )
    spans = guardrail._analyze_with_rules("ИНН 500100732259 в договоре")
    assert [span["entity_type"] for span in spans] == ["RU_INN"]


def test_checksum_keeps_a_bare_phone_out(rulebook_file):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file
    )
    assert guardrail._analyze_with_rules("телефон 987678967612") == []


def test_group_toggle_leaves_secrets_off(rulebook_file):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file, pii_rule_groups=["personal_data"]
    )
    assert guardrail._analyze_with_rules("ключ AKIA0123456789ABCDEF") == []


def test_entity_config_narrows_rule_output(rulebook_file):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        pii_rulebook=rulebook_file,
        pii_entities_config={PiiEntityType.CREDIT_CARD: PiiAction.MASK},
    )
    # Гардрейл спрашивает только карты — ИНН из рулбука в выдачу не попадает.
    assert guardrail._analyze_with_rules("ИНН 500100732259") == []


@pytest.mark.asyncio
async def test_analyze_text_merges_both_stages(rulebook_file, monkeypatch):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file
    )

    async def fake_analyzer(**kwargs):
        return [{"entity_type": "PERSON", "start": 0, "end": 7, "score": 0.85}]

    monkeypatch.setattr(guardrail, "_analyze_with_analyzer", fake_analyzer)
    results = await guardrail.analyze_text(
        text="Смирнов, ИНН 500100732259", presidio_config=None, request_data={}
    )
    assert sorted(span["entity_type"] for span in results) == ["PERSON", "RU_INN"]


@pytest.mark.asyncio
async def test_validated_rule_outscores_the_analyzer(rulebook_file, monkeypatch):
    # Дедуп перекрытий оставляет спан с большим score, поэтому контрольная сумма
    # обязана перебивать догадку NLP на том же фрагменте.
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file
    )
    text = "500100732259"

    async def fake_analyzer(**kwargs):
        return [{"entity_type": "PHONE_NUMBER", "start": 0, "end": 12, "score": 0.9}]

    monkeypatch.setattr(guardrail, "_analyze_with_analyzer", fake_analyzer)
    merged = await guardrail.analyze_text(
        text=text, presidio_config=None, request_data={}
    )
    kept = guardrail._drop_overlapping_results(merged)
    assert [span["entity_type"] for span in kept] == ["RU_INN"]


@pytest.mark.asyncio
async def test_repeated_message_is_analyzed_once(rulebook_file, monkeypatch):
    # pre_call присылает всю историю на каждом шаге хода — без кэша один и тот же
    # системный промпт уезжает в анализатор по разу на шаг.
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file
    )
    calls = []

    async def fake_analyzer(text, **kwargs):
        calls.append(text)
        return []

    monkeypatch.setattr(guardrail, "_analyze_with_analyzer", fake_analyzer)
    for _ in range(5):
        await guardrail.analyze_text(
            text="Инструкция агенту, неизменная между шагами",
            presidio_config=None,
            request_data={},
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_new_message_still_reaches_the_analyzer(rulebook_file, monkeypatch):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file
    )
    calls = []

    async def fake_analyzer(text, **kwargs):
        calls.append(text)
        return []

    monkeypatch.setattr(guardrail, "_analyze_with_analyzer", fake_analyzer)
    await guardrail.analyze_text(text="первое", presidio_config=None, request_data={})
    await guardrail.analyze_text(text="первое", presidio_config=None, request_data={})
    await guardrail.analyze_text(text="второе", presidio_config=None, request_data={})
    assert calls == ["первое", "второе"]


@pytest.mark.asyncio
async def test_cached_spans_are_returned_intact(rulebook_file, monkeypatch):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file
    )

    async def fake_analyzer(**kwargs):
        return [{"entity_type": "PERSON", "start": 0, "end": 7, "score": 0.85}]

    monkeypatch.setattr(guardrail, "_analyze_with_analyzer", fake_analyzer)
    first = await guardrail.analyze_text(
        text="Смирнов, ИНН 500100732259", presidio_config=None, request_data={}
    )
    second = await guardrail.analyze_text(
        text="Смирнов, ИНН 500100732259", presidio_config=None, request_data={}
    )
    assert [span["entity_type"] for span in first] == [
        span["entity_type"] for span in second
    ]
    # Отдаём копию: правка результата вызывающим не должна портить закэшированное.
    first.clear()
    third = await guardrail.analyze_text(
        text="Смирнов, ИНН 500100732259", presidio_config=None, request_data={}
    )
    assert len(third) == 2


@pytest.mark.asyncio
async def test_rulebook_version_invalidates_the_cache(tmp_path, monkeypatch):
    def build(version):
        path = tmp_path / f"rb-{version}.yaml"
        path.write_text(
            RULEBOOK_YAML.replace("wiring-test", version), encoding="utf-8"
        )
        return _OPTIONAL_PresidioPIIMasking(
            mock_testing=True, pii_rulebook=str(path)
        )

    old, new = build("v1"), build("v2")
    key_old = old._span_cache_key(text="один текст", presidio_config=None, request_data={})
    key_new = new._span_cache_key(text="один текст", presidio_config=None, request_data={})
    assert key_old != key_new


@pytest.mark.asyncio
async def test_cache_is_bounded(rulebook_file, monkeypatch):
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True, pii_rulebook=rulebook_file, span_cache_size=3
    )

    async def fake_analyzer(**kwargs):
        return []

    monkeypatch.setattr(guardrail, "_analyze_with_analyzer", fake_analyzer)
    for i in range(10):
        await guardrail.analyze_text(
            text=f"сообщение {i}", presidio_config=None, request_data={}
        )
    assert len(guardrail._span_cache) == 3


def test_person_required_rejects_config_without_it():
    # Правила берут только канонические ФИО; конфигурация без PERSON выпускала бы
    # редкие и иностранные имена, обращения по имени и фамилии без имени.
    with pytest.raises(Exception, match="PERSON is required"):
        _OPTIONAL_PresidioPIIMasking(
            mock_testing=True,
            require_person_entity=True,
            pii_entities_config={PiiEntityType.CREDIT_CARD: PiiAction.MASK},
        )


def test_person_required_accepts_config_with_it():
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        require_person_entity=True,
        pii_entities_config={
            PiiEntityType.PERSON: PiiAction.MASK,
            PiiEntityType.CREDIT_CARD: PiiAction.MASK,
        },
    )
    assert guardrail.pii_entities_config


def test_person_requirement_is_opt_in():
    # Апстримное поведение не меняем: без флага конфигурация без PERSON допустима.
    guardrail = _OPTIONAL_PresidioPIIMasking(
        mock_testing=True,
        pii_entities_config={PiiEntityType.CREDIT_CARD: PiiAction.MASK},
    )
    assert guardrail.pii_entities_config


@pytest.mark.asyncio
async def test_streaming_upstream_error_is_not_swallowed(presidio_guardrail):
    """Отказ апстрима посреди стрима обязан дойти до клиента.

    Хук ловил любое исключение и отдавал накопленные чанки. Если провайдер падал
    до первого чанка, наружу уходил пустой SSE без finish_reason — по нему нельзя
    отличить перегрузку провайдера от поломки, и клиент видел молчание.
    """

    class Boom(Exception):
        pass

    async def failing_stream():
        raise Boom("Our servers are currently overloaded")
        yield  # pragma: no cover — генератор

    collected = []
    with pytest.raises(Boom):
        async for chunk in presidio_guardrail._stream_pii_unmasking(
            response=failing_stream(), request_data={"metadata": {}}
        ):
            collected.append(chunk)

    assert collected == [], "до падения апстрима чанков не было — отдавать нечего"


@pytest.mark.asyncio
async def test_streaming_error_after_chunks_keeps_them(presidio_guardrail):
    """Успевшие прийти чанки не теряются: сначала отдаём их, потом пробрасываем ошибку."""
    from litellm.types.utils import ModelResponseStream

    class Boom(Exception):
        pass

    good = ModelResponseStream(
        id="1", object="chat.completion.chunk", created=1, model="m",
        choices=[{"index": 0, "delta": {"content": "привет"}}],
    )

    async def stream_then_fail():
        yield good
        raise Boom("upstream died")

    collected = []
    with pytest.raises(Boom):
        async for chunk in presidio_guardrail._stream_pii_unmasking(
            response=stream_then_fail(), request_data={"metadata": {}}
        ):
            collected.append(chunk)

    assert collected == [good]
