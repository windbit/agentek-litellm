"""
Tests for GigaChat LiteLLM Provider

Tests message transformation, parameter handling, and response transformation.
Run with: pytest tests/llm_translation/test_gigachat.py -v
"""

import pytest


class TestGigaChatMessageTransformation:
    """Tests for message transformation (OpenAI -> GigaChat format)"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()

    def test_simple_user_message(self, config):
        """Basic user message should pass through"""
        messages = [{"role": "user", "content": "Hello"}]
        result = config._transform_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_developer_role_to_system(self, config):
        """Developer role should be converted to system"""
        messages = [{"role": "developer", "content": "You are helpful"}]
        result = config._transform_messages(messages)

        assert result[0]["role"] == "system"

    def test_system_after_first_becomes_user(self, config):
        """System message after first position should become user"""
        messages = [
            {"role": "assistant", "content": "Response"},
            {"role": "system", "content": "Additional instruction"},
        ]
        result = config._transform_messages(messages)

        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "user"  # system after first becomes user

    def test_tool_role_to_function(self, config):
        """Tool role should be converted to function"""
        messages = [{"role": "tool", "content": "result data"}]
        result = config._transform_messages(messages)

        assert result[0]["role"] == "function"

    def test_tool_content_convertation_non_string_value(self, config):
        """Non string tool content should be serialized"""
        messages = [{"role": "tool", "content": {"output": 42}}]
        result = config._transform_messages(messages)

        assert result[0]["content"] == '{"output": 42}'

    def test_tool_content_convertation_json_string_value(self, config):
        """JSON string tool content left unchanged"""
        valid_json = '{"output": "red car"}'
        messages = [{"role": "tool", "content": valid_json}]
        result = config._transform_messages(messages)

        assert result[0]["content"] == valid_json

    def test_tool_content_convertation_random_string_value(self, config):
        """Non JSON tool content should be serialized"""
        messages = [{"role": "tool", "content": "random string"}]
        result = config._transform_messages(messages)

        assert result[0]["content"] == '"random string"'

    def test_tool_calls_to_function_call(self, config):
        """tool_calls should be converted to function_call"""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Moscow"}',
                        },
                    }
                ],
            }
        ]
        result = config._transform_messages(messages)

        assert "function_call" in result[0]
        assert result[0]["function_call"]["name"] == "get_weather"
        assert result[0]["function_call"]["arguments"] == {"city": "Moscow"}
        assert "tool_calls" not in result[0]

    def test_parallel_tool_calls_preserve_all_matching_results(self, config):
        messages = [
            {"role": "user", "content": "Weather in Moscow and Berlin?"},
            {
                "role": "assistant",
                "content": "Checking both cities.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Moscow"}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Berlin"}',
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_2", "content": "Berlin: 18C"},
            {"role": "tool", "tool_call_id": "call_1", "content": "Moscow: 15C"},
        ]

        result = config._transform_messages(messages)

        assert [message["role"] for message in result] == [
            "user",
            "assistant",
            "function",
            "assistant",
            "function",
        ]
        assert result[1]["function_call"]["arguments"] == {"city": "Moscow"}
        assert result[1]["content"] == "Checking both cities."
        assert result[2]["tool_call_id"] == "call_1"
        assert result[2]["content"] == '"Moscow: 15C"'
        assert result[3]["function_call"]["arguments"] == {"city": "Berlin"}
        assert result[3]["content"] == ""
        assert result[4]["tool_call_id"] == "call_2"
        assert result[4]["content"] == '"Berlin: 18C"'

    def test_parallel_tool_calls_without_all_results_keep_original_history(
        self, config
    ):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Moscow"}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Berlin"}',
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "Moscow: 15C"},
        ]

        result = config._transform_messages(messages)

        assert [message["role"] for message in result] == ["assistant", "function"]
        assert result[0]["function_call"]["arguments"] == {"city": "Moscow"}
        assert result[1]["tool_call_id"] == "call_1"

    def test_none_content_becomes_empty_string(self, config):
        """None content should become empty string"""
        messages = [{"role": "assistant", "content": None}]
        result = config._transform_messages(messages)

        assert result[0]["content"] == ""

    def test_name_field_removed(self, config):
        """name field should be removed (not supported by GigaChat)"""
        messages = [{"role": "user", "content": "Hi", "name": "John"}]
        result = config._transform_messages(messages)

        assert "name" not in result[0]


class TestGigaChatCollapseUserMessages:
    """Tests for collapsing consecutive user messages"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()


class TestGigaChatToolsTransformation:
    """Tests for tools -> functions conversion"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()

    def test_single_tool_conversion(self, config):
        """Single tool should be converted correctly"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            }
        ]
        result = config._convert_tools_to_functions(tools)

        assert len(result) == 1
        assert result[0]["name"] == "get_weather"
        assert result[0]["description"] == "Get weather for a city"

    def test_multiple_tools_conversion(self, config):
        """Multiple tools should all be converted"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "func1",
                    "description": "First",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "func2",
                    "description": "Second",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        result = config._convert_tools_to_functions(tools)

        assert len(result) == 2
        assert result[0]["name"] == "func1"
        assert result[1]["name"] == "func2"


class TestGigaChatToolSchemaSanitization:
    """Tests for tool JSON Schema sanitization (windbit/issues#1613)"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()

    def _params(self, config, properties):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "notify_user",
                    "description": "Notify the user",
                    "parameters": {"type": "object", "properties": properties},
                },
            }
        ]
        return config._convert_tools_to_functions(tools)[0]["parameters"]

    def test_any_of_with_null_collapses_to_single_type(self, config):
        """Optional-параметр из pydantic/zod: anyOf с null -> одиночный тип"""
        params = self._params(
            config,
            {
                "notify": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": "d",
                }
            },
        )

        assert params["properties"]["notify"] == {"type": "boolean", "description": "d"}

    def test_type_list_keeps_first_non_null(self, config):
        params = self._params(config, {"notify": {"type": ["boolean", "null"]}})

        assert params["properties"]["notify"]["type"] == "boolean"

    def test_null_type_becomes_fallback(self, config):
        params = self._params(config, {"notify": {"type": "null"}})

        assert params["properties"]["notify"]["type"] == "string"

    def test_type_inferred_when_union_leaves_none(self, config):
        """Тип выводится из формы узла, а не подставляется строкой наугад"""
        params = self._params(
            config,
            {
                "payload": {
                    "anyOf": [
                        {"properties": {"a": {"type": "string"}}},
                        {"type": "null"},
                    ]
                },
                "tags": {"items": {"type": "string"}},
                "level": {"enum": [1, 2, 3]},
            },
        )
        properties = params["properties"]

        assert properties["payload"]["type"] == "object"
        assert properties["tags"]["type"] == "array"
        assert properties["level"]["type"] == "integer"

    def test_nested_schemas_sanitized(self, config):
        params = self._params(
            config,
            {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "flag": {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
                        },
                    },
                }
            },
        )

        item_schema = params["properties"]["items"]["items"]
        assert item_schema["properties"]["flag"] == {"type": "boolean"}

    def test_ref_is_inlined_and_defs_dropped(self, config):
        """GigaChat не разбирает $ref/$defs, поэтому схема уезжает развёрнутой"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "description": "d",
                    "parameters": {
                        "type": "object",
                        "properties": {"ref": {"$ref": "#/$defs/Flag"}},
                        "$defs": {
                            "Flag": {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
                        },
                    },
                },
            }
        ]
        params = config._convert_tools_to_functions(tools)[0]["parameters"]

        assert "$defs" not in params
        assert params["properties"]["ref"] == {"type": "boolean"}

    def test_recursive_ref_terminates(self, config):
        """Циклическая ссылка обрывается пустым object, а не рекурсией"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "description": "d",
                    "parameters": {
                        "type": "object",
                        "properties": {"node": {"$ref": "#/$defs/Node"}},
                        "$defs": {
                            "Node": {
                                "type": "object",
                                "properties": {"child": {"$ref": "#/$defs/Node"}},
                            }
                        },
                    },
                },
            }
        ]
        params = config._convert_tools_to_functions(tools)[0]["parameters"]
        node = params["properties"]["node"]

        assert node["type"] == "object"
        assert node["properties"]["child"] == {"type": "object", "properties": {}}

    def test_valid_schema_unchanged(self, config):
        properties = {"city": {"type": "string", "description": "City"}}
        params = self._params(config, properties)

        assert params == {"type": "object", "properties": properties}

    def test_structured_output_schema_sanitized(self, config):
        params = config.map_openai_params(
            non_default_params={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "person",
                        "schema": {
                            "type": "object",
                            "properties": {"age": {"type": ["integer", "null"]}},
                        },
                    },
                }
            },
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )

        assert (
            params["functions"][0]["parameters"]["properties"]["age"]["type"]
            == "integer"
        )

    def test_object_without_properties_gets_empty_properties(self, config):
        """GigaChat требует properties у object-узла, иначе 422"""
        params = self._params(config, {"arguments": {"type": "object"}})

        assert params["properties"]["arguments"] == {"type": "object", "properties": {}}

    def test_optional_object_keeps_properties_after_collapse(self, config):
        """Схлопнутый Optional-объект тоже должен получить properties"""
        params = self._params(
            config,
            {"arguments": {"anyOf": [{"type": "object"}, {"type": "null"}]}},
        )

        assert params["properties"]["arguments"] == {"type": "object", "properties": {}}

    def test_all_of_branches_merged(self, config):
        """allOf — пересечение, поэтому ветки сливаются, а не выбирается первая"""
        params = self._params(
            config,
            {
                "target": {
                    "allOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                        {"description": "merged"},
                    ]
                }
            },
        )
        target = params["properties"]["target"]

        assert target["type"] == "object"
        assert target["description"] == "merged"
        assert target["properties"]["a"] == {"type": "string"}

    def test_functions_passthrough_sanitized(self, config):
        """Схемы, пришедшие в legacy-параметре functions, тоже чистятся"""
        result = config.map_openai_params(
            non_default_params={
                "functions": [
                    {
                        "name": "notify_user",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "notify": {
                                    "anyOf": [{"type": "boolean"}, {"type": "null"}]
                                }
                            },
                        },
                    }
                ]
            },
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )

        assert result["functions"][0]["parameters"]["properties"]["notify"] == {
            "type": "boolean"
        }


class TestGigaChatParamsTransformation:
    """Tests for parameter transformation"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()

    def test_temperature_zero_becomes_top_p_zero(self, config):
        """temperature=0 should become top_p=0"""
        params = {"temperature": 0}
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )

        assert "top_p" in result
        assert result["top_p"] == 0
        assert "temperature" not in result

    def test_temperature_nonzero_preserved(self, config):
        """Non-zero temperature should be preserved"""
        params = {"temperature": 0.7}
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )

        assert result["temperature"] == 0.7

    def test_max_completion_tokens_to_max_tokens(self, config):
        """max_completion_tokens should become max_tokens"""
        params = {"max_completion_tokens": 100}
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )

        assert result["max_tokens"] == 100

    def test_structured_output_via_json_schema(self, config):
        """json_schema response_format should trigger structured output mode"""
        params = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "person",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                        },
                    },
                },
            }
        }
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )

        assert "_structured_output" in result
        assert result["_structured_output"] is True
        assert "function_call" in result
        assert result["function_call"]["name"] == "person"


class TestGigaChatProviderRegistration:
    """Tests for provider registration in LiteLLM"""

    def test_gigachat_in_provider_list(self):
        """GigaChat should be in provider list"""
        from litellm.types.utils import LlmProviders

        assert hasattr(LlmProviders, "GIGACHAT")
        assert LlmProviders.GIGACHAT.value == "gigachat"

    def test_gigachat_in_chat_providers(self):
        """GigaChat should be in LITELLM_CHAT_PROVIDERS"""
        from litellm.constants import LITELLM_CHAT_PROVIDERS

        assert "gigachat" in LITELLM_CHAT_PROVIDERS

    def test_gigachat_key_exists(self):
        """gigachat_key should be available"""
        import litellm

        assert hasattr(litellm, "gigachat_key")

    def test_gigachat_config_exists(self):
        """GigaChatConfig should be available"""
        import litellm

        assert hasattr(litellm, "GigaChatConfig")


class TestGigaChatTransformRequest:
    """Tests for request transformation"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()

    def test_basic_request(self, config):
        """Basic request should be transformed correctly"""
        messages = [{"role": "user", "content": "Hello"}]
        result = config.transform_request(
            model="gigachat/GigaChat",
            messages=messages,
            optional_params={},
            litellm_params={},
            headers={},
        )

        assert result["model"] == "GigaChat"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"

    def test_request_with_temperature(self, config):
        """Request with temperature should include it"""
        messages = [{"role": "user", "content": "Hello"}]
        result = config.transform_request(
            model="gigachat/GigaChat",
            messages=messages,
            optional_params={"temperature": 0.7},
            litellm_params={},
            headers={},
        )

        assert result["temperature"] == 0.7

    def test_request_with_functions(self, config):
        """Request with functions should include them"""
        messages = [{"role": "user", "content": "Hello"}]
        functions = [{"name": "test", "description": "Test", "parameters": {}}]
        result = config.transform_request(
            model="gigachat/GigaChat",
            messages=messages,
            optional_params={"functions": functions},
            litellm_params={},
            headers={},
        )

        assert "functions" in result
        assert len(result["functions"]) == 1


class TestGigaChatSupportedParams:
    """Tests for supported parameters"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()

    def test_supported_params(self, config):
        """Check supported parameters list"""
        supported = config.get_supported_openai_params("GigaChat")

        assert "temperature" in supported
        assert "max_tokens" in supported
        assert "max_completion_tokens" in supported
        assert "tools" in supported
        assert "response_format" in supported
        assert "stream" in supported


class TestGigaChatToolChoiceMapping:
    """Tests for tool_choice -> function_call mapping"""

    @pytest.fixture
    def config(self):
        from litellm.llms.gigachat.chat.transformation import GigaChatConfig

        return GigaChatConfig()

    def test_tool_choice_none(self, config):
        """tool_choice='none' should map to function_call='none'"""
        result = config._map_tool_choice("none")
        assert result == "none"

    def test_tool_choice_auto(self, config):
        """tool_choice='auto' should map to function_call='auto'"""
        result = config._map_tool_choice("auto")
        assert result == "auto"

    def test_tool_choice_required(self, config):
        """tool_choice='required' should map to function_call='auto' (closest equivalent)"""
        result = config._map_tool_choice("required")
        assert result == "auto"

    def test_tool_choice_forced_function(self, config):
        """tool_choice with forced function should map to function_call with name"""
        tool_choice = {"type": "function", "function": {"name": "get_weather"}}
        result = config._map_tool_choice(tool_choice)
        assert result == {"name": "get_weather"}

    def test_tool_choice_forced_function_full(self, config):
        """tool_choice with full function details should extract only name"""
        tool_choice = {
            "type": "function",
            "function": {
                "name": "weather_forecast",
                "description": "Get weather forecast",
            },
        }
        result = config._map_tool_choice(tool_choice)
        assert result == {"name": "weather_forecast"}

    def test_tool_choice_invalid_dict(self, config):
        """tool_choice with invalid dict should return None"""
        tool_choice = {"type": "tool"}  # Missing function
        result = config._map_tool_choice(tool_choice)
        assert result is None

    def test_tool_choice_in_map_openai_params_auto(self, config):
        """tool_choice='auto' should be mapped in map_openai_params"""
        params = {"tool_choice": "auto"}
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )
        assert result["function_call"] == "auto"

    def test_tool_choice_in_map_openai_params_none(self, config):
        """tool_choice='none' should be mapped in map_openai_params"""
        params = {"tool_choice": "none"}
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )
        assert result["function_call"] == "none"

    def test_tool_choice_in_map_openai_params_required(self, config):
        """tool_choice='required' should be mapped to 'auto' in map_openai_params"""
        params = {"tool_choice": "required"}
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )
        assert result["function_call"] == "auto"

    def test_tool_choice_in_map_openai_params_forced(self, config):
        """tool_choice with forced function should be mapped in map_openai_params"""
        params = {
            "tool_choice": {
                "type": "function",
                "function": {"name": "weather_forecast"},
            }
        }
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )
        assert result["function_call"] == {"name": "weather_forecast"}

    def test_tool_choice_with_tools(self, config):
        """tool_choice should work together with tools parameter"""
        params = {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        }
        result = config.map_openai_params(
            non_default_params=params,
            optional_params={},
            model="GigaChat",
            drop_params=False,
        )
        assert "functions" in result
        assert result["function_call"] == {"name": "get_weather"}

    def test_transform_request_with_tool_choice(self, config):
        """Full transform_request should include function_call from tool_choice"""
        messages = [{"role": "user", "content": "What's the weather?"}]
        optional_params = {
            "functions": [
                {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            "function_call": {"name": "get_weather"},
        }
        result = config.transform_request(
            model="gigachat/GigaChat",
            messages=messages,
            optional_params=optional_params,
            litellm_params={},
            headers={},
        )
        assert "function_call" in result
        assert result["function_call"] == {"name": "get_weather"}
