import litellm
import pytest
from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.types.utils import CredentialItem
from litellm.utils import load_credentials_from_list

from agentek_gateway.subscriptions.credential_runtime import LiteLLMCredentialRuntime

from .catalog_stack import auth_of


@pytest.fixture
def credential_list():  # type: ignore[no-untyped-def]
    saved = list(litellm.credential_list)
    litellm.credential_list = []
    yield litellm.credential_list
    litellm.credential_list = saved


def request_params(name: str) -> dict[str, object]:
    kwargs: dict[str, object] = {"litellm_credential_name": name}
    load_credentials_from_list(kwargs)
    return kwargs


def test_a_request_right_after_reauthorization_is_built_with_the_new_token(
    credential_list,  # type: ignore[no-untyped-def]
) -> None:
    CredentialAccessor.upsert_credentials(
        [
            CredentialItem(
                credential_name="team-a",
                credential_values={
                    "chatgpt_auth": {
                        "access_token": "access-secret-old",
                        "refresh_token": "r",
                    },
                    "keep": "me",
                },
                credential_info={"custom_llm_provider": "chatgpt"},
            )
        ]
    )

    LiteLLMCredentialRuntime().apply("team-a", "chatgpt", auth_of("new"))

    params = request_params("team-a")
    assert (params["chatgpt_auth"]["access_token"], params["keep"]) == (  # type: ignore[index]
        "access-secret-new",
        "me",
    )


def test_a_credential_the_worker_has_not_loaded_yet_becomes_resolvable(
    credential_list,  # type: ignore[no-untyped-def]
) -> None:
    LiteLLMCredentialRuntime().apply("fresh", "chatgpt", auth_of("1"))

    assert request_params("fresh")["chatgpt_auth"]["access_token"] == "access-secret-1"  # type: ignore[index]
