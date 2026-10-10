import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

PARAM_PREFIX = "agentek_provider:"
CONCURRENCY_FIELD = "concurrency_limit"


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    concurrency_limit: int | None = None


class ProviderSettingsRepo(Protocol):
    async def load(self) -> Mapping[str, ProviderSettings]: ...

    async def set_concurrency(self, provider: str, limit: int | None) -> None: ...


class ConfigRow(Protocol):
    param_name: str
    param_value: object


class ConfigTable(Protocol):
    """The slice of the generated Prisma delegate for LiteLLM_Config the settings use."""

    async def find_many(
        self, *, where: Mapping[str, object]
    ) -> Sequence[ConfigRow]: ...

    async def upsert(
        self, *, where: Mapping[str, object], data: Mapping[str, object]
    ) -> object: ...


class PrismaProviderSettingsRepo:
    """Operator-set provider values, one LiteLLM_Config row per provider: they survive a restart and reach every replica."""

    def __init__(self, table: Callable[[], ConfigTable]) -> None:
        self._table = table

    async def load(self) -> Mapping[str, ProviderSettings]:
        rows = await self._table().find_many(
            where={"param_name": {"startswith": PARAM_PREFIX}}
        )
        return {
            row.param_name.removeprefix(PARAM_PREFIX): _settings_of(row.param_value)
            for row in rows
        }

    async def set_concurrency(self, provider: str, limit: int | None) -> None:
        value = json.dumps({CONCURRENCY_FIELD: limit})
        name = f"{PARAM_PREFIX}{provider}"
        await self._table().upsert(
            where={"param_name": name},
            data={
                "create": {"param_name": name, "param_value": value},
                "update": {"param_value": value},
            },
        )


def _settings_of(raw: object) -> ProviderSettings:
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    limit = parsed.get(CONCURRENCY_FIELD) if isinstance(parsed, Mapping) else None
    valid = isinstance(limit, int) and not isinstance(limit, bool) and limit > 0
    return ProviderSettings(limit if valid else None)
