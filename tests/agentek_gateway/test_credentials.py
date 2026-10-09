import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentek_gateway.subscriptions.credentials import (
    InMemoryCredentialStore,
    PrismaCredentialStore,
    auth_from_mapping,
)
from agentek_gateway.subscriptions.providers.chatgpt import ChatgptAuth

START = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
STORED_AUTH = {
    "access_token": "at-old",
    "refresh_token": "rt-old",
    "id_token": "id-old",
    "expires_at": 1_800_000_000,
    "account_id": "acct",
    "device_code_requested_at": 5,
}


class FakeCredentialTable:
    def __init__(self, values: object) -> None:
        self.row = SimpleNamespace(credential_values=values, updated_at=START)
        self.updates: list[dict[str, object]] = []

    async def find_unique(self, *, where):  # type: ignore[no-untyped-def]
        return self.row if where["credential_name"] == "cred-a" else None

    async def update_many(self, *, where, data):  # type: ignore[no-untyped-def]
        if (
            where["credential_name"] != "cred-a"
            or where["updated_at"] != self.row.updated_at
        ):
            return 0
        self.updates.append(dict(data))
        self.row.credential_values = json.loads(data["credential_values"])
        self.row.updated_at += timedelta(milliseconds=1)
        return 1


def prisma_store(
    values: object | None = None,
) -> tuple[PrismaCredentialStore, FakeCredentialTable]:
    table = FakeCredentialTable(
        values
        if values is not None
        else {"chatgpt_auth": dict(STORED_AUTH), "chatgpt_api_base": "http://x"}
    )
    return PrismaCredentialStore(lambda: table), table  # type: ignore[arg-type]


async def test_tokens_are_read_with_the_row_version() -> None:
    store, _ = prisma_store()

    stored = await store.read_auth("cred-a")

    assert (stored.auth.refresh_token, stored.auth.account_id, stored.version) == (  # type: ignore[union-attr]
        "rt-old",
        "acct",
        START.isoformat(),
    )


async def test_credential_values_stored_as_json_text_are_understood() -> None:
    store, _ = prisma_store(json.dumps({"chatgpt_auth": STORED_AUTH}))

    stored = await store.read_auth("cred-a")

    assert stored is not None and stored.auth.access_token == "at-old"


@pytest.mark.parametrize(
    "values",
    [{}, {"chatgpt_auth": "not-a-mapping"}, {"chatgpt_auth": {"access_token": "x"}}],
)
async def test_row_without_usable_tokens_reads_as_none(values: object) -> None:
    store, _ = prisma_store(values)

    assert await store.read_auth("cred-a") is None


async def test_unknown_credential_reads_as_none() -> None:
    store, _ = prisma_store()

    assert await store.read_auth("other") is None


async def test_write_keeps_the_other_values_and_fields_of_the_record() -> None:
    store, table = prisma_store()
    stored = await store.read_auth("cred-a")

    written = await store.write_auth_if_unchanged(
        "cred-a", stored, ChatgptAuth("at-new", "rt-new", expires_at=1_900_000_000.0)  # type: ignore[arg-type]
    )

    values = table.row.credential_values
    assert (written, values["chatgpt_api_base"], values["chatgpt_auth"]) == (
        True,
        "http://x",
        {
            **STORED_AUTH,
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "expires_at": 1_900_000_000.0,
        },
    )


async def test_write_against_a_changed_row_is_refused() -> None:
    store, table = prisma_store()
    stored = await store.read_auth("cred-a")
    table.row.updated_at += timedelta(seconds=1)

    written = await store.write_auth_if_unchanged("cred-a", stored, ChatgptAuth("a", "b"))  # type: ignore[arg-type]

    assert (written, table.updates) == (False, [])


async def test_second_write_with_the_old_version_is_refused() -> None:
    store, _ = prisma_store()
    stored = await store.read_auth("cred-a")

    first = await store.write_auth_if_unchanged("cred-a", stored, ChatgptAuth("a1", "r1"))  # type: ignore[arg-type]
    second = await store.write_auth_if_unchanged("cred-a", stored, ChatgptAuth("a2", "r2"))  # type: ignore[arg-type]

    assert (first, second) == (True, False)


async def test_in_memory_store_refuses_a_stale_version_like_the_real_one() -> None:
    store = InMemoryCredentialStore()
    store.put("cred-a", ChatgptAuth("a0", "r0"))
    stored = await store.read_auth("cred-a")

    results = [
        await store.write_auth_if_unchanged("cred-a", stored, ChatgptAuth("a1", "r1")),  # type: ignore[arg-type]
        await store.write_auth_if_unchanged("cred-a", stored, ChatgptAuth("a2", "r2")),  # type: ignore[arg-type]
    ]

    assert results == [True, False]


def test_boolean_is_not_an_expiry_time() -> None:
    auth = auth_from_mapping(
        {"access_token": "a", "refresh_token": "r", "expires_at": True}
    )

    assert auth is not None and auth.expires_at is None
