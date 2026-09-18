"""
OpenAI Responses API Handler for Unified Guardrails

This module provides a class-based handler for OpenAI Responses API format.
The class methods can be overridden for custom behavior.

Pattern Overview:
-----------------
1. Extract text content from input/output (both string and list formats)
2. Create async tasks to apply guardrails to each text segment
3. Track mappings to know where each response belongs
4. Apply guardrail responses back to the original structure

Responses API Format:
---------------------
Input: Union[str, List[Dict]] where each dict has:
  - role: str
  - content: Union[str, List[Dict]] (can have text items)
  - type: str (e.g., "message")

Output: response.output is List[GenericResponseOutputItem] where each has:
  - type: str (e.g., "message")
  - id: str
  - status: str
  - role: str
  - content: List[OutputText] where OutputText has:
    - type: str (e.g., "output_text")
    - text: str
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union, cast

from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall
from pydantic import BaseModel

from litellm._logging import verbose_proxy_logger
from litellm.completion_extras.litellm_responses_transformation.transformation import (
    OpenAiResponsesToChatCompletionStreamIterator,
)
from litellm.llms.base_llm.guardrail_translation.base_translation import BaseTranslation
from litellm.llms.base_llm.guardrail_translation.utils import (
    effective_skip_system_message_for_guardrail,
    effective_skip_tool_message_for_guardrail,
    openai_messages_without_system,
    openai_messages_without_tool,
)
from litellm.responses.litellm_completion_transformation.transformation import (
    LiteLLMCompletionResponsesConfig,
)
from litellm.types.llms.openai import (
    AllMessageValues,
    ChatCompletionToolCallChunk,
    ChatCompletionToolParam,
)
from litellm.types.responses.main import (
    GenericResponseOutputItem,
    OutputFunctionToolCall,
    OutputText,
)
from litellm.types.utils import GenericGuardrailAPIInputs

if TYPE_CHECKING:
    from litellm.integrations.custom_guardrail import CustomGuardrail
    from litellm.types.llms.openai import ResponseInputParam
    from litellm.types.utils import ResponsesAPIResponse

_TextSlot = Tuple[Dict[str, Any], str]

_TOOL_CALL_TEXT_FIELDS = {"function_call": "arguments", "custom_tool_call": "input"}
_TOOL_OUTPUT_TYPES = ("function_call_output", "custom_tool_call_output")


class OpenAIResponsesHandler(BaseTranslation):
    """
    Handler for processing OpenAI Responses API with guardrails.

    This class provides methods to:
    1. Process input (pre-call hook)
    2. Process output response (post-call hook)

    Methods can be overridden to customize behavior for different message formats.
    """

    def get_structured_messages(self, data: dict) -> Optional[List[AllMessageValues]]:
        """
        Convert Responses API request data to OpenAI-spec structured messages.

        Transforms `input` (string or ResponseInputParam) and optional
        `instructions` into chat completion messages.
        """
        input_data = data.get("input")
        if input_data is None:
            return None
        messages = (
            LiteLLMCompletionResponsesConfig.transform_responses_api_input_to_messages(
                input=input_data,
                responses_api_request=data,
            )
        )
        return cast(List[AllMessageValues], messages) if messages else None

    async def process_input_messages(
        self,
        data: dict,
        guardrail_to_apply: "CustomGuardrail",
        litellm_logging_obj: Optional[Any] = None,
    ) -> Any:
        """
        Process input by applying guardrails to text content.

        Handles both string input and list of message objects.
        """
        input_data: Optional[Union[str, "ResponseInputParam"]] = data.get("input")
        if input_data is None or not isinstance(input_data, (str, list)):
            return data

        skip_system = effective_skip_system_message_for_guardrail(guardrail_to_apply)
        skip_tool = effective_skip_tool_message_for_guardrail(guardrail_to_apply)
        text_slots: List[_TextSlot] = []
        images_to_check: List[str] = []
        if isinstance(data.get("instructions"), str) and not skip_system:
            text_slots.append((data, "instructions"))
        if isinstance(input_data, str):
            text_slots.append((data, "input"))
        else:
            for message in input_data:
                if skip_system and message.get("role") == "system":
                    continue
                if skip_tool and message.get("type") in _TOOL_OUTPUT_TYPES:
                    continue
                self._extract_input_text_and_images(
                    message=message,
                    text_slots=text_slots,
                    images_to_check=images_to_check,
                )

        if not text_slots:
            return data

        inputs = GenericGuardrailAPIInputs(
            texts=[container[key] for container, key in text_slots]
        )
        if images_to_check:
            inputs["images"] = images_to_check
        original_tools: List[Dict[str, Any]] = list(data.get("tools") or [])
        tools_to_check: List[ChatCompletionToolParam] = []
        if original_tools:
            self._extract_and_transform_tools(data["tools"], tools_to_check)
            if tools_to_check:
                inputs["tools"] = tools_to_check
        structured_messages = self.get_structured_messages(data)
        if structured_messages and skip_system:
            structured_messages = openai_messages_without_system(structured_messages)
        if structured_messages and skip_tool:
            structured_messages = openai_messages_without_tool(structured_messages)
        if structured_messages:
            inputs["structured_messages"] = structured_messages  # type: ignore
        model = data.get("model")
        if model:
            inputs["model"] = model

        guardrailed_inputs = await guardrail_to_apply.apply_guardrail(
            inputs=inputs,
            request_data=data,
            input_type="request",
            logging_obj=litellm_logging_obj,
        )

        self._apply_guardrailed_tools_to_data(
            data, original_tools, guardrailed_inputs.get("tools")
        )
        for (container, key), guardrailed_text in zip(
            text_slots, guardrailed_inputs.get("texts", [])
        ):
            container[key] = guardrailed_text

        verbose_proxy_logger.debug(
            "OpenAI Responses API: Processed input messages: %s", data.get("input")
        )

        return data

    def extract_request_tool_names(self, data: dict) -> List[str]:
        """Extract tool names from Responses API request (tools[].name for function, tools[].server_label for mcp)."""
        names: List[str] = []
        for tool in data.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and tool.get("name"):
                names.append(str(tool["name"]))
            elif tool.get("type") == "mcp" and tool.get("server_label"):
                names.append(str(tool["server_label"]))
        return names

    def _extract_and_transform_tools(
        self,
        tools: List[Dict[str, Any]],
        tools_to_check: List[ChatCompletionToolParam],
    ) -> None:
        """
        Extract and transform tools from Responses API format to Chat Completion format.

        Uses the LiteLLM transformation function to convert Responses API tools
        to Chat Completion tools that can be passed to guardrails.
        """
        if tools is not None and isinstance(tools, list):
            # Transform Responses API tools to Chat Completion tools
            (
                transformed_tools,
                _,
            ) = LiteLLMCompletionResponsesConfig.transform_responses_api_tools_to_chat_completion_tools(
                tools  # type: ignore
            )
            tools_to_check.extend(
                cast(List[ChatCompletionToolParam], transformed_tools)
            )

    def _remap_tools_to_responses_api_format(
        self, guardrailed_tools: List[Any]
    ) -> List[Dict[str, Any]]:
        """
        Remap guardrail-returned tools (Chat Completion format) back to
        Responses API request tool format.
        """
        return LiteLLMCompletionResponsesConfig.transform_chat_completion_tool_params_to_responses_api_tools(
            guardrailed_tools  # type: ignore
        )

    def _merge_tools_after_guardrail(
        self,
        original_tools: List[Dict[str, Any]],
        remapped: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge remapped guardrailed tools with original tools that were not sent
        to the guardrail (e.g. web_search, web_search_preview), preserving order.
        """
        if not original_tools:
            return remapped
        result: List[Dict[str, Any]] = []
        j = 0
        for tool in original_tools:
            if isinstance(tool, dict) and tool.get("type") in (
                "web_search",
                "web_search_preview",
            ):
                result.append(tool)
            else:
                if j < len(remapped):
                    result.append(remapped[j])
                    j += 1
        return result

    def _apply_guardrailed_tools_to_data(
        self,
        data: dict,
        original_tools: List[Dict[str, Any]],
        guardrailed_tools: Optional[List[Any]],
    ) -> None:
        """Remap guardrailed tools to Responses API format and merge with original, then set data['tools']."""
        if guardrailed_tools is not None:
            remapped = self._remap_tools_to_responses_api_format(guardrailed_tools)
            data["tools"] = self._merge_tools_after_guardrail(original_tools, remapped)

    def _extract_input_text_and_images(
        self,
        message: Any,  # Can be Dict[str, Any] or ResponseInputParam
        text_slots: List[_TextSlot],
        images_to_check: List[str],
    ) -> None:
        """
        Extract text locations and images from an input item.

        Override this method to customize text/image extraction logic.
        """
        item_type = message.get("type")
        if item_type in _TOOL_OUTPUT_TYPES:
            self._append_text_slots(message, "output", text_slots)
            return
        if item_type in _TOOL_CALL_TEXT_FIELDS:
            self._append_text_slots(
                message, _TOOL_CALL_TEXT_FIELDS[item_type], text_slots
            )
            return
        self._append_text_slots(message, "summary", text_slots)
        self._append_text_slots(message, "content", text_slots)

        for content_item in message.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            if content_item.get("type") == "image_url":
                image_url = content_item.get("image_url", {})
                if isinstance(image_url, dict) and image_url.get("url"):
                    images_to_check.append(image_url["url"])

    @staticmethod
    def _append_text_slots(
        container: Dict[str, Any], key: str, text_slots: List[_TextSlot]
    ) -> None:
        value = container.get(key)
        if isinstance(value, str):
            text_slots.append((container, key))
        elif isinstance(value, list):
            text_slots.extend(
                (part, "text")
                for part in value
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )

    async def process_output_response(
        self,
        response: "ResponsesAPIResponse",
        guardrail_to_apply: "CustomGuardrail",
        litellm_logging_obj: Optional[Any] = None,
        user_api_key_dict: Optional[Any] = None,
        request_data: Optional[dict] = None,
    ) -> Any:
        """
        Process output response by applying guardrails to text content and tool calls.

        Args:
            response: LiteLLM ResponsesAPIResponse object
            guardrail_to_apply: The guardrail instance to apply
            litellm_logging_obj: Optional logging object
            user_api_key_dict: User API key metadata to pass to guardrails

        Returns:
            Modified response with guardrail applied to content

        Response Format Support:
            - response.output is a list of output items
            - Each output item can be:
              * GenericResponseOutputItem with a content list of OutputText objects
              * ResponseFunctionToolCall with tool call data
            - Each OutputText object has a text field
        """

        texts_to_check: List[str] = []
        images_to_check: List[str] = []
        tool_calls_to_check: List[ChatCompletionToolCallChunk] = []
        task_mappings: List[Tuple[int, int]] = []
        # Track (output_item_index, content_index) for each text

        # Handle both dict and Pydantic object responses
        if isinstance(response, dict):
            response_output = response.get("output", [])
        elif hasattr(response, "output"):
            response_output = response.output or []
        else:
            verbose_proxy_logger.debug(
                "OpenAI Responses API: No output found in response"
            )
            return response

        if not response_output:
            verbose_proxy_logger.debug("OpenAI Responses API: Empty output in response")
            return response

        tool_call_output_indexes: List[int] = []

        # Step 1: Extract all text content and tool calls from response output
        for output_idx, output_item in enumerate(response_output):
            tool_calls_before = len(tool_calls_to_check)
            self._extract_output_text_and_images(
                output_item=output_item,
                output_idx=output_idx,
                texts_to_check=texts_to_check,
                images_to_check=images_to_check,
                task_mappings=task_mappings,
                tool_calls_to_check=tool_calls_to_check,
            )
            if len(tool_calls_to_check) > tool_calls_before:
                tool_call_output_indexes.append(output_idx)

        # Step 2: Apply guardrail to all texts in batch
        if texts_to_check or tool_calls_to_check:
            # Use the real request_data if provided (proxy path), otherwise
            # create a standalone dict (SDK / direct-call path).
            if request_data is None:
                request_data = {"response": response}
            else:
                if "response" not in request_data:
                    request_data["response"] = response

            # Add user API key metadata with prefixed keys
            if "litellm_metadata" not in request_data:
                user_metadata = self.transform_user_api_key_dict_to_metadata(
                    user_api_key_dict
                )
                if user_metadata:
                    request_data["litellm_metadata"] = user_metadata

            inputs = GenericGuardrailAPIInputs(texts=texts_to_check)
            if images_to_check:
                inputs["images"] = images_to_check
            if tool_calls_to_check:
                inputs["tool_calls"] = tool_calls_to_check
            # Include model information from the response if available
            response_model = None
            if isinstance(response, dict):
                response_model = response.get("model")
            elif hasattr(response, "model"):
                response_model = getattr(response, "model", None)
            if response_model:
                inputs["model"] = response_model

            guardrailed_inputs = await guardrail_to_apply.apply_guardrail(
                inputs=inputs,
                request_data=request_data,
                input_type="response",
                logging_obj=litellm_logging_obj,
            )

            guardrailed_texts = guardrailed_inputs.get("texts", [])

            # Step 3: Map guardrail responses back to original response structure
            await self._apply_guardrail_responses_to_output(
                response=response,
                responses=guardrailed_texts,
                task_mappings=task_mappings,
            )
            self._apply_guardrailed_tool_calls_to_output(
                response_output=response_output,
                tool_calls=guardrailed_inputs.get("tool_calls") or [],
                output_indexes=tool_call_output_indexes,
            )

        verbose_proxy_logger.debug(
            "OpenAI Responses API: Processed output response: %s", response
        )

        return response

    @staticmethod
    def _apply_guardrailed_tool_calls_to_output(
        response_output: List[Any],
        tool_calls: List[ChatCompletionToolCallChunk],
        output_indexes: List[int],
    ) -> None:
        for output_idx, tool_call in zip(output_indexes, tool_calls):
            arguments = tool_call["function"]["arguments"]
            output_item = response_output[output_idx]
            if isinstance(output_item, dict):
                output_item["arguments"] = arguments
            else:
                output_item.arguments = arguments

    async def process_output_streaming_response(
        self,
        responses_so_far: List[Any],
        guardrail_to_apply: "CustomGuardrail",
        litellm_logging_obj: Optional[Any] = None,
        user_api_key_dict: Optional[Any] = None,
        request_data: Optional[dict] = None,
    ) -> List[Any]:
        """
        Process output streaming response by applying guardrails to text content.

        Mirrors the Chat Completions handler pattern: extract text from the final
        chunk, apply the guardrail, then write the result back in-place so the
        caller sees the modified content (e.g. PII tokens replaced).

        For ``response.completed`` events (the normal end-of-stream signal) we
        use the same per-item extraction + task-mapping approach as
        ``process_output_response`` so that unmasking / blocking works correctly
        for every output item.
        """
        if not responses_so_far:
            return responses_so_far

        final_chunk = responses_so_far[-1]
        # Accept both plain dicts and Pydantic models (BaseLiteLLMOpenAIResponseObject
        # exposes a .get() shim, so all the .get() calls below work for both).
        if not (isinstance(final_chunk, dict) or hasattr(final_chunk, "get")):
            return responses_so_far

        # ------------------------------------------------------------------ #
        # Case 1: response.completed — full response is available in the      #
        # final chunk; iterate output items, apply guardrail, write back.     #
        # ------------------------------------------------------------------ #
        if final_chunk.get("type") == "response.completed":
            response_obj = final_chunk.get("response") or {}
            if not hasattr(response_obj, "get"):
                return responses_so_far
            outputs: List[Any] = response_obj.get("output") or []

            texts_to_check: List[str] = []
            tool_calls_to_check: List[ChatCompletionToolCallChunk] = []
            task_mappings: List[Tuple[int, int]] = []

            for output_idx, output_item in enumerate(outputs):
                self._extract_output_text_and_images(
                    output_item=output_item,
                    output_idx=output_idx,
                    texts_to_check=texts_to_check,
                    images_to_check=[],
                    task_mappings=task_mappings,
                    tool_calls_to_check=tool_calls_to_check,
                )

            if texts_to_check or tool_calls_to_check:
                if request_data is None:
                    request_data = {}
                if "response" not in request_data:
                    request_data["response"] = response_obj
                if "litellm_metadata" not in request_data:
                    user_metadata = self.transform_user_api_key_dict_to_metadata(
                        user_api_key_dict
                    )
                    if user_metadata:
                        request_data["litellm_metadata"] = user_metadata

                inputs = GenericGuardrailAPIInputs(texts=texts_to_check)
                if tool_calls_to_check:
                    inputs["tool_calls"] = cast(
                        List[ChatCompletionToolCallChunk], tool_calls_to_check
                    )
                response_model = response_obj.get("model")
                if response_model:
                    inputs["model"] = response_model

                guardrailed_inputs = await guardrail_to_apply.apply_guardrail(
                    inputs=inputs,
                    request_data=request_data,
                    input_type="response",
                    logging_obj=litellm_logging_obj,
                )

                guardrailed_texts = guardrailed_inputs.get("texts", [])

                # Write guardrailed texts back into the output items in-place.
                # final_chunk is a reference into responses_so_far so this
                # mutates the list that the caller holds.
                await self._apply_guardrail_responses_to_output(
                    response=response_obj,
                    responses=guardrailed_texts,
                    task_mappings=task_mappings,
                )

            return responses_so_far

        # ------------------------------------------------------------------ #
        # Case 2: response.output_item.done — extract tool calls only.        #
        # ------------------------------------------------------------------ #
        if final_chunk.get("type") == "response.output_item.done":
            model_response_stream = OpenAiResponsesToChatCompletionStreamIterator.translate_responses_chunk_to_openai_stream(
                final_chunk
            )
            tool_calls = model_response_stream.choices[0].delta.tool_calls
            if tool_calls:
                inputs = GenericGuardrailAPIInputs()
                inputs["tool_calls"] = cast(
                    List[ChatCompletionToolCallChunk], tool_calls
                )
                if (
                    hasattr(model_response_stream, "model")
                    and model_response_stream.model
                ):
                    inputs["model"] = model_response_stream.model
                await guardrail_to_apply.apply_guardrail(
                    inputs=inputs,
                    request_data=request_data if request_data is not None else {},
                    input_type="response",
                    logging_obj=litellm_logging_obj,
                )
            return responses_so_far

        # ------------------------------------------------------------------ #
        # Fallback: apply guardrail to the accumulated text string.           #
        # No structured write-back is possible here; guardrails that only     #
        # need to block/flag (not rewrite) still work correctly.             #
        # ------------------------------------------------------------------ #
        string_so_far = self.get_streaming_string_so_far(responses_so_far)
        if string_so_far:
            fallback_inputs = GenericGuardrailAPIInputs(texts=[string_so_far])
            response_model = (
                final_chunk.get("response", {}).get("model")
                if isinstance(final_chunk.get("response"), dict)
                else None
            )
            if response_model:
                fallback_inputs["model"] = response_model
            await guardrail_to_apply.apply_guardrail(
                inputs=fallback_inputs,
                request_data=request_data if request_data is not None else {},
                input_type="response",
                logging_obj=litellm_logging_obj,
            )
        return responses_so_far

    def _check_streaming_has_ended(self, responses_so_far: List[Any]) -> bool:
        """
        Check if the streaming has ended.
        """
        return all(
            response.choices[0].finish_reason is not None
            for response in responses_so_far
        )

    def get_streaming_string_so_far(self, responses_so_far: List[Any]) -> str:
        """
        Get the string so far from the responses so far.
        """
        return "".join([response.get("text", "") for response in responses_so_far])

    def _has_text_content(self, response: "ResponsesAPIResponse") -> bool:
        """
        Check if response has any text content to process.

        Override this method to customize text content detection.
        """
        if not hasattr(response, "output") or response.output is None:
            return False

        for output_item in response.output:
            if isinstance(output_item, BaseModel):
                try:
                    generic_response_output_item = (
                        GenericResponseOutputItem.model_validate(
                            output_item.model_dump()
                        )
                    )
                    if generic_response_output_item.content:
                        output_item = generic_response_output_item
                except Exception:
                    continue
            if isinstance(output_item, (GenericResponseOutputItem, dict)):
                content = (
                    output_item.content
                    if isinstance(output_item, GenericResponseOutputItem)
                    else output_item.get("content", [])
                )
                if content:
                    for content_item in content:
                        # Check if it's an OutputText with text
                        if isinstance(content_item, OutputText):
                            if content_item.text:
                                return True
                        elif isinstance(content_item, dict):
                            if content_item.get("text"):
                                return True
        return False

    def _extract_output_text_and_images(
        self,
        output_item: Any,
        output_idx: int,
        texts_to_check: List[str],
        images_to_check: List[str],
        task_mappings: List[Tuple[int, int]],
        tool_calls_to_check: Optional[List[ChatCompletionToolCallChunk]] = None,
    ) -> None:
        """
        Extract text content, images, and tool calls from a response output item.

        Override this method to customize text/image/tool extraction logic.
        """

        # Check if this is a tool call (OutputFunctionToolCall)
        if isinstance(output_item, OutputFunctionToolCall):
            if tool_calls_to_check is not None:
                tool_call_dict = LiteLLMCompletionResponsesConfig.convert_response_function_tool_call_to_chat_completion_tool_call(
                    tool_call_item=output_item,
                    index=output_idx,
                )
                tool_calls_to_check.append(
                    cast(ChatCompletionToolCallChunk, tool_call_dict)
                )
            return
        elif (
            isinstance(output_item, BaseModel)
            and hasattr(output_item, "type")
            and getattr(output_item, "type") == "function_call"
        ):
            if tool_calls_to_check is not None:
                tool_call_dict = LiteLLMCompletionResponsesConfig.convert_response_function_tool_call_to_chat_completion_tool_call(
                    tool_call_item=output_item,
                    index=output_idx,
                )
                tool_calls_to_check.append(
                    cast(ChatCompletionToolCallChunk, tool_call_dict)
                )
            return
        elif (
            isinstance(output_item, dict) and output_item.get("type") == "function_call"
        ):
            # Handle dict representation of tool call
            if tool_calls_to_check is not None:
                # Convert dict to ResponseFunctionToolCall for processing
                try:
                    tool_call_obj = ResponseFunctionToolCall(**output_item)
                    tool_call_dict = LiteLLMCompletionResponsesConfig.convert_response_function_tool_call_to_chat_completion_tool_call(
                        tool_call_item=tool_call_obj,
                        index=output_idx,
                    )
                    tool_calls_to_check.append(
                        cast(ChatCompletionToolCallChunk, tool_call_dict)
                    )
                except Exception:
                    pass
            return

        # Handle both GenericResponseOutputItem and dict
        content: Optional[Union[List[OutputText], List[dict]]] = None
        if isinstance(output_item, BaseModel):
            try:
                output_item_dump = output_item.model_dump()
                generic_response_output_item = GenericResponseOutputItem.model_validate(
                    output_item_dump
                )
                if generic_response_output_item.content:
                    content = generic_response_output_item.content
            except Exception:
                # Try to extract content directly from output_item if validation fails
                if hasattr(output_item, "content") and output_item.content:  # type: ignore
                    content = output_item.content  # type: ignore
                else:
                    return
        elif isinstance(output_item, dict):
            content = output_item.get("content", [])
        else:
            return

        if not content:
            return

        verbose_proxy_logger.debug(
            "OpenAI Responses API: Processing output item: %s", output_item
        )

        # Iterate through content items (list of OutputText objects)
        for content_idx, content_item in enumerate(content):
            # Handle both OutputText objects and dicts
            if isinstance(content_item, OutputText):
                text_content = content_item.text
            elif isinstance(content_item, dict):
                text_content = content_item.get("text")
            else:
                continue

            if text_content:
                texts_to_check.append(text_content)
                task_mappings.append((output_idx, int(content_idx)))

    async def _apply_guardrail_responses_to_output(
        self,
        response: Union["ResponsesAPIResponse", Dict[Any, Any]],
        responses: List[str],
        task_mappings: List[Tuple[int, int]],
    ) -> None:
        """
        Apply guardrail responses back to output response.

        Override this method to customize how responses are applied.
        """
        # Handle both dict and Pydantic object responses
        if isinstance(response, dict):
            response_output = response.get("output", [])
        elif hasattr(response, "output"):
            response_output = response.output or []
        else:
            return

        for task_idx, guardrail_response in enumerate(responses):
            mapping = task_mappings[task_idx]
            output_idx = cast(int, mapping[0])
            content_idx = cast(int, mapping[1])

            if output_idx >= len(response_output):
                continue

            output_item = response_output[output_idx]

            # Handle both GenericResponseOutputItem, BaseModel, and dict
            if isinstance(output_item, GenericResponseOutputItem):
                if output_item.content and content_idx < len(output_item.content):
                    content_item = output_item.content[content_idx]
                    if isinstance(content_item, OutputText):
                        content_item.text = guardrail_response
                    elif isinstance(content_item, dict):
                        content_item["text"] = guardrail_response
            elif isinstance(output_item, BaseModel):
                # Handle other Pydantic models by converting to GenericResponseOutputItem
                try:
                    generic_item = GenericResponseOutputItem.model_validate(
                        output_item.model_dump()
                    )
                    if generic_item.content and content_idx < len(generic_item.content):
                        content_item = generic_item.content[content_idx]
                        if isinstance(content_item, OutputText):
                            content_item.text = guardrail_response
                            # Update the original response output
                            if hasattr(output_item, "content") and output_item.content:  # type: ignore
                                original_content = output_item.content[content_idx]  # type: ignore
                                if hasattr(original_content, "text"):
                                    original_content.text = guardrail_response  # type: ignore
                except Exception:
                    pass
            elif isinstance(output_item, dict):
                content = output_item.get("content", [])
                if content and content_idx < len(content):
                    if isinstance(content[content_idx], dict):
                        content[content_idx]["text"] = guardrail_response
                    elif hasattr(content[content_idx], "text"):
                        content[content_idx].text = guardrail_response
