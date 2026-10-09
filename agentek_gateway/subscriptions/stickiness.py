import hashlib
import uuid
from collections.abc import Mapping

from .clock import Clock
from .expiring import ExpiringMap
from .model import SubscriptionId
from .ports import StateStore

PROMPT_CACHE_KEY = "prompt_cache_key"
SESSION_ID_PARAM = "chatgpt_session_id"
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


class StickyBook:
    """Chat-to-subscription bindings; the shared store is read only on a local miss and written only on change."""

    def __init__(
        self, store: StateStore, clock: Clock, ttl_s: float, local_ttl_s: float
    ) -> None:
        self._store = store
        self._ttl_s = ttl_s
        self._local: ExpiringMap[str, SubscriptionId] = ExpiringMap(clock, local_ttl_s)

    async def lookup(self, prompt_cache_key: str) -> SubscriptionId | None:
        key = sticky_store_key(prompt_cache_key)
        known = self._local.get(key)
        if known:
            return known
        stored = await self._store.read_sticky(key)
        if stored:
            self._local.put(key, stored)
        return stored

    async def bind(
        self, prompt_cache_key: str, subscription_id: SubscriptionId
    ) -> None:
        key = sticky_store_key(prompt_cache_key)
        if self._local.get(key) == subscription_id:
            return
        self._local.put(key, subscription_id)
        await self._store.write_sticky(key, subscription_id, self._ttl_s)
