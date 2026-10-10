import json
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from prisma.errors import UniqueViolationError

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper
from litellm.proxy.management_endpoints.model_management_endpoints import (
    _add_model_to_db,
)
from litellm.types.router import Deployment, LiteLLM_Params, ModelInfo

from .model_copies import COPY_ID_PREFIX, TEMPLATE_ID_PREFIX, ModelRow

ACTOR = "agentek_gateway"


class ModelRecord(Protocol):
    model_id: str
    model_name: str
    litellm_params: object
    model_info: object


class ModelTable(Protocol):
    """The slice of the generated Prisma delegate for LiteLLM_ProxyModelTable the store uses."""

    async def find_many(
        self, *, where: Mapping[str, object]
    ) -> Sequence[ModelRecord]: ...

    async def update(
        self, *, where: Mapping[str, object], data: Mapping[str, object]
    ) -> object: ...

    async def delete_many(self, *, where: Mapping[str, object]) -> int: ...


class ProxyDb(Protocol):
    """What LiteLLM's own model writer needs from the proxy's database client."""

    db: object


class PrismaModelStore:
    """Template and copy rows of LiteLLM_ProxyModelTable.

    Rows are written by LiteLLM's own writer, so litellm_params are encrypted exactly as for a model added in the UI.
    """

    def __init__(
        self, table: Callable[[], ModelTable], proxy_db: Callable[[], ProxyDb]
    ) -> None:
        self._table = table
        self._proxy_db = proxy_db

    async def list_templates(self) -> Sequence[ModelRow]:
        return await self._rows(TEMPLATE_ID_PREFIX)

    async def list_copies(self) -> Sequence[ModelRow]:
        return await self._rows(COPY_ID_PREFIX)

    async def create_copy(self, row: ModelRow) -> bool:
        try:
            await _add_model_to_db(
                model_params=_deployment_of(row),
                user_api_key_dict=UserAPIKeyAuth(user_id=ACTOR),
                prisma_client=self._proxy_db(),  # type: ignore[arg-type]
            )
        except UniqueViolationError:
            return False
        return True

    async def update_copy(self, row: ModelRow) -> None:
        stored = await _add_model_to_db(
            model_params=_deployment_of(row),
            user_api_key_dict=UserAPIKeyAuth(user_id=ACTOR),
            prisma_client=self._proxy_db(),  # type: ignore[arg-type]
            should_create_model_in_db=False,
        )
        assert stored is not None
        await self._table().update(
            where={"model_id": row.model_id},
            data={
                "litellm_params": json.dumps(stored.litellm_params),
                "model_info": json.dumps(stored.model_info),
                "updated_by": ACTOR,
            },
        )

    async def delete_copies(self, model_ids: Sequence[str]) -> None:
        await self._table().delete_many(
            where={"model_id": {"in": list(model_ids), "startswith": COPY_ID_PREFIX}}
        )

    async def _rows(self, prefix: str) -> Sequence[ModelRow]:
        records = await self._table().find_many(
            where={"model_id": {"startswith": prefix}}
        )
        return tuple(_row_of(record) for record in records)


def _deployment_of(row: ModelRow) -> Deployment:
    return Deployment(
        model_name=row.model_name,
        litellm_params=LiteLLM_Params(**row.litellm_params),  # type: ignore[arg-type]
        model_info=ModelInfo(**row.model_info),  # type: ignore[arg-type]
    )


def _row_of(record: ModelRecord) -> ModelRow:
    params = _object_of(record.litellm_params)
    return ModelRow(
        model_id=record.model_id,
        model_name=record.model_name,
        litellm_params={key: _decrypted(key, value) for key, value in params.items()},
        model_info=_object_of(record.model_info),
    )


def _decrypted(key: str, value: object) -> object:
    return decrypt_value_helper(
        value,  # type: ignore[arg-type]
        key=key,
        exception_type="debug",
        return_original_value=True,
    )


def _object_of(raw: object) -> dict[str, object]:
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return dict(parsed) if isinstance(parsed, Mapping) else {}
