import asyncio
from collections.abc import Mapping

from litellm._logging import verbose_proxy_logger

from .credentials import CredentialStore
from .ports import SubscriptionRepo
from .providers.chatgpt import ChatgptAuth

PAIR_REFRESH_INTERVAL_S = 5.0


class CredentialPairs:
    """Token pairs as the database holds them, readable from the authenticator's synchronous path."""

    def __init__(self) -> None:
        self._pairs: Mapping[str, ChatgptAuth] = {}

    def read(self, credential_name: str) -> ChatgptAuth | None:
        return self._pairs.get(credential_name)

    def replace(self, pairs: Mapping[str, ChatgptAuth]) -> None:
        self._pairs = pairs


class CredentialPairsLoop:
    """Keeps CredentialPairs close to the database on every replica, independently of the proxy's own credential reload."""

    def __init__(
        self,
        pairs: CredentialPairs,
        repo: SubscriptionRepo,
        credentials: CredentialStore,
        interval_s: float = PAIR_REFRESH_INTERVAL_S,
    ) -> None:
        self._pairs = pairs
        self._repo = repo
        self._credentials = credentials
        self._interval_s = interval_s

    async def run(self) -> None:
        while True:
            try:
                await self.refresh_once()
            except Exception:  # noqa: BLE001
                verbose_proxy_logger.exception(
                    "agentek_gateway credential pairs refresh failed"
                )
            await asyncio.sleep(self._interval_s)

    async def refresh_once(self) -> None:
        names = [sub.credential_name for sub in await self._repo.list_subscriptions()]
        stored = await self._credentials.read_auths(names)
        self._pairs.replace({name: item.auth for name, item in stored.items()})
