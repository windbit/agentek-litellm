from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from .audit import AuditLog, PrismaAuditLog
from .credential_directory import CredentialDirectory, PrismaCredentialDirectory
from .prisma_repos import PrismaSubscriptionWriter, SubscriptionWriter
from .provider_settings import PrismaProviderSettingsRepo, ProviderSettingsRepo


@dataclass(frozen=True, slots=True)
class Writes:
    """The database writes of one operator action; in a real database they share a transaction with the audit entry."""

    writer: SubscriptionWriter
    directory: CredentialDirectory
    settings: ProviderSettingsRepo
    audit: AuditLog


Unit = Callable[[], AbstractAsyncContextManager[Writes]]


class TransactionalDb(Protocol):
    def tx(self) -> AbstractAsyncContextManager["TransactionalDb"]: ...


class PrismaUnit:
    """An exception inside the block rolls every write of the action back, the audit entry included."""

    def __init__(self, db: Callable[[], TransactionalDb]) -> None:
        self._db = db

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[Writes]:
        async with self._db().tx() as tx:
            yield Writes(
                writer=PrismaSubscriptionWriter(lambda: tx.litellm_agenteksubscription),  # type: ignore[attr-defined]
                directory=PrismaCredentialDirectory(lambda: tx.litellm_credentialstable),  # type: ignore[attr-defined]
                settings=PrismaProviderSettingsRepo(lambda: tx.litellm_config),  # type: ignore[attr-defined]
                audit=PrismaAuditLog(lambda: tx.litellm_agentekaudit),  # type: ignore[attr-defined]
            )


def fixed_unit(writes: Writes) -> Unit:
    @asynccontextmanager
    async def unit() -> AsyncIterator[Writes]:
        yield writes

    return unit
