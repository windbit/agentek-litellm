from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth

from .startup import GATEWAY_STATE

API_PREFIX = "/agentek"


async def require_proxy_admin(
    auth: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> UserAPIKeyAuth:
    if auth.user_role != LitellmUserRoles.PROXY_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return auth


def build_api_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_proxy_admin)])

    @router.get("/status")
    async def plugin_status() -> dict[str, str]:
        ready = GATEWAY_STATE.ready.is_set()
        return {
            "plugin": "agentek_gateway",
            "status": "up",
            "ready": str(ready).lower(),
        }

    return router
