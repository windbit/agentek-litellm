from typing import Any, List, Optional, Tuple

from litellm.exceptions import AuthenticationError
from litellm.llms.openai.openai import OpenAIConfig
from litellm.types.llms.openai import AllMessageValues

from ..authenticator import Authenticator
from ..common_utils import (
    GetAccessTokenError,
    chatgpt_credential_requested,
    ensure_chatgpt_session_id,
    get_chatgpt_default_headers,
    merge_chatgpt_request_headers,
)
from .streaming_utils import ChatGPTToolCallNormalizer


class ChatGPTConfig(OpenAIConfig):
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        custom_llm_provider: str = "openai",
    ) -> None:
        super().__init__()
        self.authenticator = Authenticator()

    def _get_authenticator(self, litellm_params: Optional[Any]) -> Authenticator:
        """Resolve the per-deployment credential, falling back to the global
        authenticator only when no credential was requested."""
        if chatgpt_credential_requested(litellm_params):
            return Authenticator.from_litellm_params(litellm_params)
        return self.authenticator

    def _get_openai_compatible_provider_info(
        self,
        model: str,
        api_base: Optional[str],
        api_key: Optional[str],
        custom_llm_provider: str,
    ) -> Tuple[Optional[str], Optional[str], str]:
        # Provider discovery (get_llm_provider) runs before per-deployment
        # credentials are resolved. We resolve only the API base here and defer
        # token resolution to the request hot-path (main.py for chat,
        # validate_environment for /responses). This honors per-deployment
        # credentials and prevents discovery from triggering a network token
        # refresh or an interactive device login.
        return self.authenticator.get_api_base(), api_key, custom_llm_provider

    def validate_environment(
        self,
        headers: dict,
        model: str,
        messages: List[AllMessageValues],
        optional_params: dict,
        litellm_params: dict,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> dict:
        authenticator = self._get_authenticator(litellm_params)
        try:
            access_token = authenticator.get_access_token()
        except GetAccessTokenError as e:
            raise AuthenticationError(
                model=model,
                llm_provider="chatgpt",
                message=str(e),
            )

        validated_headers = super().validate_environment(
            headers,
            model,
            messages,
            optional_params,
            litellm_params,
            access_token,
            api_base,
        )

        account_id = authenticator.get_account_id()
        session_id = ensure_chatgpt_session_id(litellm_params)
        default_headers = get_chatgpt_default_headers(
            access_token, account_id, session_id
        )
        # User-supplied headers may not override credential-derived auth headers.
        return merge_chatgpt_request_headers(default_headers, validated_headers)

    def post_stream_processing(self, stream: Any) -> Any:
        return ChatGPTToolCallNormalizer(stream)

    def map_openai_params(
        self,
        non_default_params: dict,
        optional_params: dict,
        model: str,
        drop_params: bool,
    ) -> dict:
        optional_params = super().map_openai_params(
            non_default_params, optional_params, model, drop_params
        )
        optional_params.setdefault("stream", False)
        return optional_params
