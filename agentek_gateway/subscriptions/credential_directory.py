import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from prisma import Json
from prisma.errors import UniqueViolationError

from .credentials import AUTH_KEY, UPDATED_BY, auth_from_mapping, auth_to_mapping
from .providers.chatgpt import ChatgptAuth

PROVIDER_INFO_KEY = "custom_llm_provider"
DISABLED_INFO_KEY = "disabled"


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    name: str
    has_tokens: bool
    disabled: bool


class CredentialDirectory(Protocol):
    """Credentials of one provider as LiteLLM stores them: the import reads, login and removal write."""

    async def list_credentials(self, provider: str) -> Sequence[CredentialRecord]: ...

    async def create_credential(
        self, name: str, provider: str, auth: ChatgptAuth
    ) -> bool:
        """False when a credential with this name exists."""
        ...

    async def replace_auth(self, name: str, auth: ChatgptAuth) -> bool:
        """False when no credential has this name."""
        ...

    async def delete_credential(self, name: str) -> None: ...


class DirectoryRow(Protocol):
    credential_name: str
    credential_values: object
    credential_info: object


class DirectoryTable(Protocol):
    """The slice of the generated Prisma delegate the directory uses."""

    async def find_many(
        self, *, where: Mapping[str, object]
    ) -> Sequence[DirectoryRow]: ...

    async def create(self, *, data: Mapping[str, object]) -> object: ...

    async def update_many(
        self, *, where: Mapping[str, object], data: Mapping[str, object]
    ) -> int: ...

    async def delete_many(self, *, where: Mapping[str, object]) -> int: ...


class PrismaCredentialDirectory:
    def __init__(self, table: Callable[[], DirectoryTable]) -> None:
        self._table = table

    async def list_credentials(self, provider: str) -> Sequence[CredentialRecord]:
        rows = await self._table().find_many(
            where={
                "credential_info": {
                    "path": [PROVIDER_INFO_KEY],
                    "equals": Json(provider),
                }
            }
        )
        return tuple(_record_of(row) for row in rows)

    async def create_credential(
        self, name: str, provider: str, auth: ChatgptAuth
    ) -> bool:
        try:
            await self._table().create(
                data={
                    "credential_name": name,
                    "credential_values": json.dumps({AUTH_KEY: auth_to_mapping(auth)}),
                    "credential_info": json.dumps({PROVIDER_INFO_KEY: provider}),
                    "created_by": UPDATED_BY,
                    "updated_by": UPDATED_BY,
                }
            )
        except UniqueViolationError:
            return False
        return True

    async def replace_auth(self, name: str, auth: ChatgptAuth) -> bool:
        rows = await self._table().find_many(where={"credential_name": name})
        if not rows:
            return False
        values = _object_of(rows[0].credential_values)
        await self._table().update_many(
            where={"credential_name": name},
            data={
                "credential_values": json.dumps(
                    {**values, AUTH_KEY: auth_to_mapping(auth)}
                ),
                "updated_by": UPDATED_BY,
            },
        )
        return True

    async def delete_credential(self, name: str) -> None:
        await self._table().delete_many(where={"credential_name": name})


def _record_of(row: DirectoryRow) -> CredentialRecord:
    values = _object_of(row.credential_values)
    info = _object_of(row.credential_info)
    return CredentialRecord(
        name=row.credential_name,
        has_tokens=auth_from_mapping(values.get(AUTH_KEY)) is not None,
        disabled=info.get(DISABLED_INFO_KEY) is True,
    )


def _object_of(raw: object) -> dict[str, object]:
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return dict(parsed) if isinstance(parsed, Mapping) else {}
