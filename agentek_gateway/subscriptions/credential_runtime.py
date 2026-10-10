from typing import Protocol

import litellm
from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.types.utils import CredentialItem

from .credentials import AUTH_KEY, auth_to_mapping
from .providers.chatgpt import ChatgptAuth

PROVIDER_INFO_KEY = "custom_llm_provider"


class CredentialRuntime(Protocol):
    """The credentials this worker resolves requests against; the database reaches them only on LiteLLM's 30 s reload."""

    def apply(self, name: str, provider: str, auth: ChatgptAuth) -> None: ...


class LiteLLMCredentialRuntime:
    """Updates litellm.credential_list the way the credentials endpoint does after it writes the database."""

    def apply(self, name: str, provider: str, auth: ChatgptAuth) -> None:
        existing = next(
            (item for item in litellm.credential_list if item.credential_name == name),
            None,
        )
        values = dict(existing.credential_values) if existing else {}
        info = dict(existing.credential_info or {}) if existing else {}
        CredentialAccessor.upsert_credentials(
            [
                CredentialItem(
                    credential_name=name,
                    credential_values={**values, AUTH_KEY: auth_to_mapping(auth)},
                    credential_info={PROVIDER_INFO_KEY: provider, **info},
                )
            ]
        )


class NullCredentialRuntime:
    def apply(self, name: str, provider: str, auth: ChatgptAuth) -> None:
        return None
