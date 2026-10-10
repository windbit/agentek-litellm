from fastapi import APIRouter, Depends

from .auth import require_proxy_admin
from .startup import GATEWAY_STATE
from .subscriptions.admin import ADMIN_SLOT, AdminSlot
from .subscriptions.policy_admin import POLICY_ADMIN_SLOT, PolicyAdminSlot
from .subscriptions.routes import subscriptions_router

API_PREFIX = "/agentek"


def build_api_router(
    admin_slot: AdminSlot = ADMIN_SLOT,
    policy_slot: PolicyAdminSlot = POLICY_ADMIN_SLOT,
) -> APIRouter:
    router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_proxy_admin)])
    router.include_router(
        subscriptions_router(lambda: admin_slot.admin, lambda: policy_slot.admin),
        prefix="/subscriptions",
    )

    @router.get("/status")
    async def plugin_status() -> dict[str, str]:
        ready = GATEWAY_STATE.ready.is_set()
        return {
            "plugin": "agentek_gateway",
            "status": "up",
            "ready": str(ready).lower(),
        }

    return router
