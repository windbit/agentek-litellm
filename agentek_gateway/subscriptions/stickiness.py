import hashlib
import uuid
from collections.abc import Mapping

PROMPT_CACHE_KEY = "prompt_cache_key"
SESSION_ID_PARAM = "litellm_session_id"
SESSION_NAMESPACE = uuid.UUID("5c1ad3a0-6b0e-5d5f-9f55-0a6a9c1e7a11")


def prompt_cache_key_of(request_kwargs: Mapping[str, object]) -> str | None:
    value = request_kwargs.get(PROMPT_CACHE_KEY)
    return value if isinstance(value, str) and value else None


def sticky_store_key(prompt_cache_key: str) -> str:
    return hashlib.sha256(prompt_cache_key.encode()).hexdigest()


def session_id_for(prompt_cache_key: str) -> str:
    return str(uuid.uuid5(SESSION_NAMESPACE, prompt_cache_key))


def with_session_id(kwargs: Mapping[str, object]) -> dict[str, object] | None:
    key = prompt_cache_key_of(kwargs)
    if key is None:
        return None
    return {**kwargs, SESSION_ID_PARAM: session_id_for(key)}
