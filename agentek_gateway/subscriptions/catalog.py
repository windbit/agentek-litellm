from collections.abc import Sequence
from dataclasses import dataclass

from litellm._logging import verbose_proxy_logger

from .audit import AuditLog
from .credential_directory import CredentialDirectory
from .importer import CredentialImporter
from .model_copies import CopySync, ModelStore
from .prisma_repos import SubscriptionWriter
from .provider_settings import ProviderSettingsRepo


@dataclass(frozen=True, slots=True)
class Catalog:
    """What the operator API and the catalog upkeep write through; absent where only selection runs."""

    directory: CredentialDirectory
    writer: SubscriptionWriter
    models: ModelStore
    audit: AuditLog
    settings: ProviderSettingsRepo


class CatalogUpkeep:
    """Brings subscriptions and their deployments in line with credentials and model templates."""

    def __init__(
        self,
        importer: CredentialImporter,
        copies: CopySync,
        providers: Sequence[str],
    ) -> None:
        self._importer = importer
        self._copies = copies
        self._providers = providers

    async def tick(self) -> None:
        for provider in self._providers:
            report = await self._importer.run(provider)
            if report.imported:
                verbose_proxy_logger.info(
                    "agentek_gateway imported %d credentials of %s as subscriptions",
                    len(report.imported),
                    provider,
                )
        await self._copies.run_once()

    async def import_at_start(self) -> None:
        for provider in self._providers:
            report = await self._importer.run(provider)
            verbose_proxy_logger.info(
                "agentek_gateway credential import for %s: imported %d, already present %d, skipped without tokens %d",
                provider,
                len(report.imported),
                report.skipped_existing,
                len(report.skipped_without_tokens),
            )
        await self._copies.run_once()
