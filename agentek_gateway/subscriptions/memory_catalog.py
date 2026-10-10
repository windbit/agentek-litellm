from collections.abc import Mapping, Sequence
from dataclasses import replace

from .credential_directory import CredentialRecord
from .credentials import InMemoryCredentialStore
from .memory import InMemorySubscriptionRepo
from .model import Subscription, SubscriptionId
from .model_copies import COPY_ID_PREFIX, TEMPLATE_ID_PREFIX, ModelRow
from .prisma_repos import NewSubscription
from .provider_settings import ProviderSettings
from .providers.chatgpt import ChatgptAuth


class InMemorySubscriptionWriter:
    def __init__(self, repo: InMemorySubscriptionRepo) -> None:
        self._repo = repo
        self._next_id = 0

    async def create_subscription(self, new: NewSubscription) -> Subscription | None:
        if any(sub.name == new.name for sub in await self._repo.list_subscriptions()):
            return None
        self._next_id += 1
        subscription = Subscription(
            id=f"id-{self._next_id}",
            provider=new.provider,
            name=new.name,
            credential_name=new.credential_name,
            priority=new.priority,
            enabled=new.enabled,
        )
        self._repo.put(subscription)
        return subscription

    async def update_subscription(
        self, subscription_id: SubscriptionId, fields: Mapping[str, int | None]
    ) -> None:
        current = next(
            sub
            for sub in await self._repo.list_subscriptions()
            if sub.id == subscription_id
        )
        changes: dict[str, int | None] = {}
        if "priority" in fields:
            changes["priority"] = fields["priority"]
        if "max_concurrency" in fields:
            changes["concurrency_limit"] = fields["max_concurrency"]
        self._repo.put(replace(current, **changes))  # type: ignore[arg-type]

    async def delete_subscription(self, subscription_id: SubscriptionId) -> None:
        self._repo.remove(subscription_id)


class InMemoryCredentialDirectory:
    """Credentials of one provider; tokens live in the shared InMemoryCredentialStore."""

    def __init__(self, store: InMemoryCredentialStore, provider: str) -> None:
        self._store = store
        self._provider = provider
        self.disabled: set[str] = set()
        self.empty: set[str] = set()

    def add_empty(self, name: str) -> None:
        self.empty.add(name)

    async def list_credentials(self, provider: str) -> Sequence[CredentialRecord]:
        if provider != self._provider:
            return ()
        names = [*self._store.values, *sorted(self.empty)]
        return tuple(
            CredentialRecord(
                name=name,
                has_tokens=name not in self.empty,
                disabled=name in self.disabled,
            )
            for name in names
        )

    async def create_credential(
        self, name: str, provider: str, auth: ChatgptAuth
    ) -> bool:
        if name in self._store.values or name in self.empty:
            return False
        self._store.put(name, auth)
        return True

    async def replace_auth(self, name: str, auth: ChatgptAuth) -> None:
        self._store.put(name, auth)

    async def delete_credential(self, name: str) -> None:
        self._store.values.pop(name, None)
        self.empty.discard(name)


class InMemoryModelStore:
    def __init__(self) -> None:
        self.rows: dict[str, ModelRow] = {}
        self.writes = 0

    def add_template(self, row: ModelRow) -> None:
        self.rows[row.model_id] = row

    async def list_templates(self) -> Sequence[ModelRow]:
        return self._with_prefix(TEMPLATE_ID_PREFIX)

    async def list_copies(self) -> Sequence[ModelRow]:
        return self._with_prefix(COPY_ID_PREFIX)

    async def create_copy(self, row: ModelRow) -> bool:
        if row.model_id in self.rows:
            return False
        self.rows[row.model_id] = row
        self.writes += 1
        return True

    async def update_copy(self, row: ModelRow) -> None:
        self.rows[row.model_id] = row
        self.writes += 1

    async def delete_copies(self, model_ids: Sequence[str]) -> None:
        for model_id in model_ids:
            self.rows.pop(model_id, None)

    def _with_prefix(self, prefix: str) -> Sequence[ModelRow]:
        return tuple(
            row for model_id, row in self.rows.items() if model_id.startswith(prefix)
        )


class InMemoryProviderSettings:
    def __init__(self) -> None:
        self.items: dict[str, ProviderSettings] = {}

    async def load(self) -> Mapping[str, ProviderSettings]:
        return dict(self.items)

    async def set_concurrency(self, provider: str, limit: int | None) -> None:
        self.items[provider] = ProviderSettings(limit)
