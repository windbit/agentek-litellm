from typing import Annotated, Awaitable, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from litellm.proxy._types import UserAPIKeyAuth

from ..auth import require_proxy_admin
from .admin import (
    AdminError,
    ConflictError,
    InvalidRequestError,
    LoginTarget,
    NotFoundError,
    SubscriptionAdmin,
)
from .providers.chatgpt_login import ProviderLoginError

T = TypeVar("T")

DEFAULT_ACTOR = "proxy_admin"
STATUS_BY_ERROR: dict[type[Exception], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidRequestError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ProviderLoginError: status.HTTP_502_BAD_GATEWAY,
}

Admin = Callable[[], SubscriptionAdmin | None]
Auth = Annotated[UserAPIKeyAuth, Depends(require_proxy_admin)]


class EnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: int | None = None
    max_concurrency: int | None = None


class ProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concurrency_limit: int | None = None


class LoginStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str


class LoginPollBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    device_auth_id: str = Field(min_length=1)
    user_code: str = Field(min_length=1)
    name: str | None = None
    subscription_id: str | None = None


def actor_of(auth: UserAPIKeyAuth) -> str:
    return auth.user_id or DEFAULT_ACTOR


def subscriptions_router(admin: Admin) -> APIRouter:
    router = APIRouter()

    def service() -> SubscriptionAdmin:
        current = admin()
        if current is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Subscription pool is starting"
            )
        return current

    @router.get("")
    async def list_subscriptions() -> dict[str, object]:
        providers, subscriptions = await _guarded(service().overview())
        return {
            "providers": [provider.as_json() for provider in providers],
            "subscriptions": [item.as_json() for item in subscriptions],
        }

    @router.put("/providers/{provider}")
    async def set_provider(
        provider: str, body: ProviderBody, auth: Auth
    ) -> dict[str, object]:
        view = await _guarded(
            service().set_provider_concurrency(
                actor_of(auth), provider, body.concurrency_limit
            )
        )
        return view.as_json()

    @router.post("/login/start")
    async def login_start(body: LoginStartBody) -> dict[str, str]:
        login = await _guarded(service().login_start(body.provider))
        return {
            "device_auth_id": login.device_auth_id,
            "user_code": login.user_code,
            "verify_url": login.verify_url,
        }

    @router.post("/login/poll")
    async def login_poll(body: LoginPollBody, auth: Auth) -> dict[str, object]:
        result = await _guarded(
            service().login_poll(
                actor_of(auth),
                body.provider,
                body.device_auth_id,
                body.user_code,
                LoginTarget(body.name, body.subscription_id),
            )
        )
        if not result.done or result.subscription is None:
            return {"status": "pending"}
        return {"status": "done", "subscription": result.subscription.as_json()}

    @router.get("/{subscription_id}")
    async def get_subscription(subscription_id: str) -> dict[str, object]:
        return (await _guarded(service().get(subscription_id))).as_json()

    @router.patch("/{subscription_id}")
    async def update_subscription(
        subscription_id: str, body: SettingsBody, auth: Auth
    ) -> dict[str, object]:
        changes = {name: getattr(body, name) for name in body.model_fields_set}
        view = await _guarded(
            service().update_settings(actor_of(auth), subscription_id, changes)
        )
        return view.as_json()

    @router.put("/{subscription_id}/enabled")
    async def set_enabled(
        subscription_id: str, body: EnabledBody, auth: Auth
    ) -> dict[str, object]:
        view = await _guarded(
            service().set_enabled(actor_of(auth), subscription_id, body.enabled)
        )
        return view.as_json()

    @router.post("/{subscription_id}/refresh-limits")
    async def refresh_limits(subscription_id: str, auth: Auth) -> dict[str, object]:
        result = await _guarded(
            service().refresh_limits(actor_of(auth), subscription_id)
        )
        return {
            "refreshed": result.refreshed,
            "subscription": result.subscription.as_json(),
        }

    @router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_subscription(subscription_id: str, auth: Auth) -> None:
        await _guarded(service().remove(actor_of(auth), subscription_id))

    return router


async def _guarded(work: Awaitable[T]) -> T:
    try:
        return await work
    except (AdminError, ProviderLoginError) as error:
        raise HTTPException(STATUS_BY_ERROR[type(error)], detail=str(error)) from error
