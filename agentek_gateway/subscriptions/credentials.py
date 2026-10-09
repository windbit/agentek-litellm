import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .providers.chatgpt import ChatgptAuth

AUTH_KEY = "chatgpt_auth"
UPDATED_BY = "agentek_gateway"


@dataclass(frozen=True, slots=True)
class StoredAuth:
    auth: ChatgptAuth
    version: str
    values: Mapping[str, object]


class CredentialStore(Protocol):
    async def read_auth(self, credential_name: str) -> StoredAuth | None: ...

    async def write_auth_if_unchanged(
        self, credential_name: str, expected: StoredAuth, auth: ChatgptAuth
    ) -> bool: ...


class CredentialRow(Protocol):
    credential_values: object
    updated_at: datetime


class CredentialTable(Protocol):
    """The slice of the generated Prisma delegate the store uses."""

    async def find_unique(
        self, *, where: Mapping[str, str]
    ) -> CredentialRow | None: ...

    async def update_many(
        self, *, where: Mapping[str, object], data: Mapping[str, object]
    ) -> int: ...


def auth_from_mapping(raw: object) -> ChatgptAuth | None:
    if not isinstance(raw, Mapping):
        return None
    access, refresh = raw.get("access_token"), raw.get("refresh_token")
    if not (isinstance(access, str) and isinstance(refresh, str)):
        return None
    account_id, id_token, expires_at = (
        raw.get("account_id"),
        raw.get("id_token"),
        raw.get("expires_at"),
    )
    return ChatgptAuth(
        access_token=access,
        refresh_token=refresh,
        account_id=account_id if isinstance(account_id, str) else None,
        id_token=id_token if isinstance(id_token, str) else None,
        expires_at=(
            float(expires_at)
            if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool)
            else None
        ),
    )


def auth_to_mapping(auth: ChatgptAuth) -> dict[str, object]:
    """Only the fields that are known: an unknown one must not overwrite a stored value when merged."""
    fields: dict[str, object] = {
        "access_token": auth.access_token,
        "refresh_token": auth.refresh_token,
        "id_token": auth.id_token,
        "expires_at": auth.expires_at,
        "account_id": auth.account_id,
    }
    return {name: value for name, value in fields.items() if value is not None}


def merged_values(stored: StoredAuth, auth: ChatgptAuth) -> dict[str, object]:
    current = stored.values.get(AUTH_KEY)
    base = dict(current) if isinstance(current, Mapping) else {}
    return {**stored.values, AUTH_KEY: {**base, **auth_to_mapping(auth)}}


class InMemoryCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}
        self.versions: dict[str, int] = {}
        self.failures_left = 0

    def put(self, credential_name: str, auth: ChatgptAuth) -> None:
        self.values[credential_name] = {AUTH_KEY: auth_to_mapping(auth)}
        self.versions[credential_name] = self.versions.get(credential_name, 0) + 1

    async def read_auth(self, credential_name: str) -> StoredAuth | None:
        values = self.values.get(credential_name)
        auth = auth_from_mapping(values.get(AUTH_KEY)) if values else None
        if values is None or auth is None:
            return None
        return StoredAuth(auth, str(self.versions[credential_name]), dict(values))

    async def write_auth_if_unchanged(
        self, credential_name: str, expected: StoredAuth, auth: ChatgptAuth
    ) -> bool:
        if self.failures_left:
            self.failures_left -= 1
            raise ConnectionError("credential store unavailable")
        if str(self.versions.get(credential_name)) != expected.version:
            return False
        self.values[credential_name] = merged_values(expected, auth)
        self.versions[credential_name] += 1
        return True


class PrismaCredentialStore:
    """Tokens of a subscription live in LiteLLM_CredentialsTable; updated_at is the version for compare-and-set."""

    def __init__(self, table: Callable[[], CredentialTable]) -> None:
        self._table = table

    async def read_auth(self, credential_name: str) -> StoredAuth | None:
        row = await self._table().find_unique(
            where={"credential_name": credential_name}
        )
        if row is None:
            return None
        values = _values_of(row.credential_values)
        auth = auth_from_mapping(values.get(AUTH_KEY))
        if auth is None:
            return None
        return StoredAuth(auth, row.updated_at.isoformat(), values)

    async def write_auth_if_unchanged(
        self, credential_name: str, expected: StoredAuth, auth: ChatgptAuth
    ) -> bool:
        changed = await self._table().update_many(
            where={
                "credential_name": credential_name,
                "updated_at": datetime.fromisoformat(expected.version),
            },
            data={
                "credential_values": json.dumps(merged_values(expected, auth)),
                "updated_by": UPDATED_BY,
            },
        )
        return changed > 0


def _values_of(raw: object) -> dict[str, object]:
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return dict(parsed) if isinstance(parsed, Mapping) else {}
