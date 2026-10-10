from typing import Annotated

from fastapi import Depends, HTTPException, status

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth


async def require_proxy_admin(
    auth: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> UserAPIKeyAuth:
    if auth.user_role != LitellmUserRoles.PROXY_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return auth
