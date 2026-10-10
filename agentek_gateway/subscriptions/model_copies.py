from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .model import Subscription
from .ports import SubscriptionRepo

TEMPLATE_ID_PREFIX = "template:"
COPY_ID_PREFIX = "sub:"
CREDENTIAL_PARAM = "litellm_credential_name"
UPSTREAM_PARAM = "model"
COPY_SYNC_INTERVAL_S = 15.0
VOLATILE_INFO_KEYS = frozenset(
    {"id", "blocked", "created_at", "created_by", "updated_at", "updated_by"}
)


@dataclass(frozen=True, slots=True)
class ModelRow:
    """A deployment as plain values: litellm_params are decrypted, the store encrypts them again on write."""

    model_id: str
    model_name: str
    litellm_params: Mapping[str, object]
    model_info: Mapping[str, object]


class ModelStore(Protocol):
    """Deployment rows of LiteLLM_ProxyModelTable: templates the console owns and copies this plugin owns."""

    async def list_templates(self) -> Sequence[ModelRow]: ...

    async def list_copies(self) -> Sequence[ModelRow]: ...

    async def create_copy(self, row: ModelRow) -> bool:
        """False when the row already exists."""
        ...

    async def update_copy(self, row: ModelRow) -> None: ...

    async def delete_copies(self, model_ids: Sequence[str]) -> None: ...

    def normalized(self, row: ModelRow) -> ModelRow:
        """The row as the store would read it back after writing it."""
        ...


@dataclass(frozen=True, slots=True)
class CopyPlan:
    create: tuple[ModelRow, ...]
    update: tuple[ModelRow, ...]
    delete: tuple[str, ...]


def copy_id(subscription: Subscription, model_name: str) -> str:
    return f"{COPY_ID_PREFIX}{subscription.id}:{model_name}"


def template_provider(template: ModelRow) -> str | None:
    upstream = template.litellm_params.get(UPSTREAM_PARAM)
    return (
        upstream.partition("/")[0]
        if isinstance(upstream, str) and "/" in upstream
        else None
    )


def copy_of(template: ModelRow, subscription: Subscription) -> ModelRow:
    model_id = copy_id(subscription, template.model_name)
    return ModelRow(
        model_id=model_id,
        model_name=template.model_name,
        litellm_params={
            **template.litellm_params,
            CREDENTIAL_PARAM: subscription.credential_name,
        },
        model_info={**_stable_info(template.model_info), "id": model_id},
    )


def _stable_info(info: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in info.items() if key not in VOLATILE_INFO_KEYS}


def _same_content(first: ModelRow, second: ModelRow) -> bool:
    return (
        first.model_name == second.model_name
        and first.litellm_params == second.litellm_params
        and _stable_info(first.model_info) == _stable_info(second.model_info)
    )


def plan_copies(
    templates: Sequence[ModelRow],
    subscriptions: Sequence[Subscription],
    existing: Sequence[ModelRow],
    normalized: Callable[[ModelRow], ModelRow] = lambda row: row,
) -> CopyPlan:
    """One copy per (subscription, template of its provider); every other copy is removed."""
    wanted = {
        row.model_id: row
        for subscription in subscriptions
        for template in templates
        if template_provider(template) == subscription.provider
        for row in (normalized(copy_of(template, subscription)),)
    }
    present = {row.model_id: row for row in existing}
    return CopyPlan(
        create=tuple(
            row for model_id, row in wanted.items() if model_id not in present
        ),
        update=tuple(
            row
            for model_id, row in wanted.items()
            if model_id in present and not _same_content(present[model_id], row)
        ),
        delete=tuple(model_id for model_id in present if model_id not in wanted),
    )


class CopySync:
    """Keeps the deployments of every (subscription, model) pair equal to the model template.

    Reads copies first, then subscriptions: a copy that was listed belongs to a subscription committed earlier,
    so a subscription added during a pass is never mistaken for a deleted one.
    """

    def __init__(self, store: ModelStore, repo: SubscriptionRepo) -> None:
        self._store = store
        self._repo = repo

    async def run_once(self) -> CopyPlan:
        existing = await self._store.list_copies()
        subscriptions = await self._repo.list_subscriptions()
        templates = await self._store.list_templates()
        plan = plan_copies(
            templates,
            subscriptions,
            [self._store.normalized(row) for row in existing],
            self._store.normalized,
        )
        raced = [row for row in plan.create if not await self._store.create_copy(row)]
        for row in (*plan.update, *raced):
            await self._store.update_copy(row)
        if plan.delete:
            await self._store.delete_copies(plan.delete)
        if plan.create or plan.update or plan.delete:
            verbose_proxy_logger.info(
                "agentek_gateway model copies created=%d updated=%d deleted=%d",
                len(plan.create),
                len(plan.update),
                len(plan.delete),
            )
        return plan

    async def remove_subscription(self, subscription: Subscription) -> None:
        prefix = f"{COPY_ID_PREFIX}{subscription.id}:"
        stale = [
            row.model_id
            for row in await self._store.list_copies()
            if row.model_id.startswith(prefix)
        ]
        if stale:
            await self._store.delete_copies(stale)
