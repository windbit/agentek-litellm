from collections.abc import Sequence
from dataclasses import dataclass

from litellm._logging import verbose_proxy_logger

from .audit import SYSTEM_ACTOR, AuditEntry, AuditLog
from .credential_directory import CredentialDirectory, CredentialRecord
from .model import Subscription
from .ports import SubscriptionRepo
from .prisma_repos import NewSubscription, SubscriptionWriter
from .toggle import SubscriptionToggle

IMPORT_ACTION = "subscription.imported"


@dataclass(frozen=True, slots=True)
class ImportReport:
    imported: tuple[str, ...]
    skipped_existing: int
    skipped_without_tokens: tuple[str, ...]


class CredentialImporter:
    """Turns the provider's existing LiteLLM credentials into subscriptions under their own names.

    Safe to run again and on several replicas at once: the unique subscription name decides, the loser counts it as existing.
    """

    def __init__(
        self,
        directory: CredentialDirectory,
        repo: SubscriptionRepo,
        writer: SubscriptionWriter,
        toggle: SubscriptionToggle,
        audit: AuditLog,
    ) -> None:
        self._directory = directory
        self._repo = repo
        self._writer = writer
        self._toggle = toggle
        self._audit = audit
        self._reported_skips: set[str] = set()

    async def run(self, provider: str) -> ImportReport:
        credentials = await self._directory.list_credentials(provider)
        subscriptions = await self._repo.list_subscriptions()
        known = {sub.name for sub in subscriptions} | {
            sub.credential_name for sub in subscriptions
        }
        imported: list[str] = []
        without_tokens: list[str] = []
        existing = 0
        for credential in credentials:
            if credential.name in known:
                existing += 1
            elif not credential.has_tokens:
                without_tokens.append(credential.name)
            elif await self._import(provider, credential):
                imported.append(credential.name)
            else:
                existing += 1
        self._report_skips(without_tokens)
        return ImportReport(tuple(imported), existing, tuple(without_tokens))

    async def _import(self, provider: str, credential: CredentialRecord) -> bool:
        subscription = await self._writer.create_subscription(
            NewSubscription(
                provider=provider,
                name=credential.name,
                credential_name=credential.name,
                enabled=not credential.disabled,
            )
        )
        if subscription is None:
            return False
        if credential.disabled:
            await self._toggle.set_enabled(subscription, False)
        await self._audit.record(self._entry(subscription))
        return True

    def _report_skips(self, names: Sequence[str]) -> None:
        for name in names:
            if name not in self._reported_skips:
                self._reported_skips.add(name)
                verbose_proxy_logger.warning(
                    "agentek_gateway import skipped credential %s: it has no tokens",
                    name,
                )

    @staticmethod
    def _entry(subscription: Subscription) -> AuditEntry:
        return AuditEntry(
            actor=SYSTEM_ACTOR,
            action=IMPORT_ACTION,
            subscription_id=subscription.id,
            subscription_name=subscription.name,
            after={
                "provider": subscription.provider,
                "enabled": subscription.enabled,
                "priority": subscription.priority,
            },
        )
