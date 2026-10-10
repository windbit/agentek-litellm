from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .clock import Clock, SystemClock
from .model import Subscription
from .ports import SubscriptionRepo

TEMPLATE_ID_PREFIX = "template:"
COPY_ID_PREFIX = "sub:"
CREDENTIAL_PARAM = "litellm_credential_name"
UPSTREAM_PARAM = "model"
COPY_SYNC_INTERVAL_S = 15.0
LEGACY_GRACE_S = 60.0
VOLATILE_INFO_KEYS = frozenset({"id", "blocked", "created_at", "created_by", "updated_at", "updated_by"})


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

    async def list_legacy(self) -> Sequence[ModelRow]:
        """Deployments an earlier writer made for a credential: neither a template nor a copy."""
        ...

    async def delete_legacy(self, model_ids: Sequence[str]) -> None: ...

    async def create_copy(self, row: ModelRow) -> bool:
        """False when the row already exists."""
        ...

    async def update_copy(self, row: ModelRow) -> None: ...

    async def delete_copies(self, model_ids: Sequence[str]) -> None: ...

    async def fully_blocked_credentials(self) -> frozenset[str]:
        """Credentials whose deployments all carry the pause flag; a credential nothing uses is not listed."""
        ...

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
    return upstream.partition("/")[0] if isinstance(upstream, str) and "/" in upstream else None


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
        create=tuple(row for model_id, row in wanted.items() if model_id not in present),
        update=tuple(
            row for model_id, row in wanted.items() if model_id in present and not _same_content(present[model_id], row)
        ),
        delete=tuple(model_id for model_id in present if model_id not in wanted),
    )


def legacy_pairs_to_retire(
    legacy: Sequence[ModelRow],
    subscriptions: Sequence[Subscription],
    settled_copies: frozenset[str],
) -> tuple[str, ...]:
    """Legacy deployments of a subscription credential whose pair already has a settled copy."""
    by_credential = {sub.credential_name: sub for sub in subscriptions}
    retired = []
    for row in legacy:
        credential = row.litellm_params.get(CREDENTIAL_PARAM)
        subscription = by_credential.get(credential) if isinstance(credential, str) else None
        if subscription and copy_id(subscription, row.model_name) in settled_copies:
            retired.append(row.model_id)
    return tuple(retired)


class CopySync:
    """Keeps the deployments of every (subscription, model) pair equal to the model template.

    Reads copies first, then subscriptions: a copy that was listed belongs to a subscription committed earlier,
    so a subscription added during a pass is never mistaken for a deleted one.

    Deployments an earlier writer made for the same pair are removed only after the copy has stood for
    LEGACY_GRACE_S: every router reloads the table every 30 s, so by then each one serves the copy already.
    """

    def __init__(
        self,
        store: ModelStore,
        repo: SubscriptionRepo,
        clock: Clock | None = None,
        legacy_grace_s: float = LEGACY_GRACE_S,
    ) -> None:
        self._store = store
        self._repo = repo
        self._clock = clock or SystemClock()
        self._legacy_grace_s = legacy_grace_s
        self._standing_since: dict[str, float] = {}

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
        await self._retire_legacy(
            subscriptions,
            {row.model_id for row in existing}.union(row.model_id for row in plan.create).difference(plan.delete),
        )
        if plan.create or plan.update or plan.delete:
            verbose_proxy_logger.info(
                "agentek_gateway model copies created=%d updated=%d deleted=%d",
                len(plan.create),
                len(plan.update),
                len(plan.delete),
            )
        return plan

    async def _retire_legacy(self, subscriptions: Sequence[Subscription], present: set[str]) -> None:
        now = self._clock.now()
        self._standing_since = {model_id: self._standing_since.get(model_id, now) for model_id in present}
        settled = frozenset(
            model_id for model_id, since in self._standing_since.items() if now - since >= self._legacy_grace_s
        )
        retired = legacy_pairs_to_retire(await self._store.list_legacy(), subscriptions, settled)
        if retired:
            await self._store.delete_legacy(retired)
            verbose_proxy_logger.info(
                "agentek_gateway took over %d deployments of subscription credentials",
                len(retired),
            )

    async def remove_subscription(self, subscription: Subscription) -> None:
        prefix = f"{COPY_ID_PREFIX}{subscription.id}:"
        stale = [row.model_id for row in await self._store.list_copies() if row.model_id.startswith(prefix)]
        if stale:
            await self._store.delete_copies(stale)
        orphaned = [
            row.model_id
            for row in await self._store.list_legacy()
            if row.litellm_params.get(CREDENTIAL_PARAM) == subscription.credential_name
        ]
        if orphaned:
            await self._store.delete_legacy(orphaned)
