import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

SYSTEM_ACTOR = "system"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    actor: str
    action: str
    subscription_id: str | None = None
    subscription_name: str | None = None
    subject: str | None = None
    before: Mapping[str, object] | None = None
    after: Mapping[str, object] | None = None


class AuditLog(Protocol):
    """Append-only record of operator actions; entries carry values that changed, never credentials."""

    async def record(self, entry: AuditEntry) -> None: ...


class AuditTable(Protocol):
    async def create(self, *, data: Mapping[str, object]) -> object: ...


class PrismaAuditLog:
    def __init__(self, table: Callable[[], AuditTable]) -> None:
        self._table = table

    async def record(self, entry: AuditEntry) -> None:
        data: dict[str, object] = {
            "actor": entry.actor,
            "action": entry.action,
            "subscription_id": entry.subscription_id,
            "subscription_name": entry.subscription_name,
            "subject": entry.subject,
        }
        if entry.before is not None:
            data["before"] = json.dumps(entry.before)
        if entry.after is not None:
            data["after"] = json.dumps(entry.after)
        await self._table().create(data=data)


class InMemoryAuditLog:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)
