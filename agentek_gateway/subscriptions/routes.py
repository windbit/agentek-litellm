from typing import Annotated, Awaitable, Callable, Self, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from litellm.proxy._types import UserAPIKeyAuth

from ..auth import require_proxy_admin
from .admin import (
    AdminError,
    ConflictError,
    InvalidRequestError,
    LoginTarget,
    NewSubscriptionTarget,
    NotFoundError,
    ReauthorizeTarget,
    SubscriptionAdmin,
)
from .providers.chatgpt_login import ProviderLoginError

T = TypeVar("T")

DEFAULT_ACTOR = "proxy_admin"
ACTOR_HEADER = "x-agentek-actor"
MAX_ACTOR_LENGTH = 128
INT4_MAX = 2**31 - 1
STATUS_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (InvalidRequestError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (ProviderLoginError, status.HTTP_502_BAD_GATEWAY),
)

Priority = Annotated[int, Field(strict=True, ge=-INT4_MAX, le=INT4_MAX)]
Limit = Annotated[int, Field(strict=True, ge=1, le=INT4_MAX)]

Admin = Callable[[], SubscriptionAdmin | None]
Auth = Annotated[UserAPIKeyAuth, Depends(require_proxy_admin)]


class EnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: Priority | None = None
    max_concurrency: Limit | None = None


class ProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concurrency_limit: Limit | None = None


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

    @model_validator(mode="after")
    def exactly_one_target(self) -> Self:
        if (self.name is None) == (self.subscription_id is None):
            raise ValueError("give either name or subscription_id")
        return self

    def target(self) -> LoginTarget:
        if self.subscription_id is not None:
            return ReauthorizeTarget(self.subscription_id)
        return NewSubscriptionTarget(self.name or "")


def actor_of(auth: Auth, request: Request) -> str:
    """Every route is admin-only, so the console's header can be trusted to name the person behind its admin key."""
    named = request.headers.get(ACTOR_HEADER, "").strip()[:MAX_ACTOR_LENGTH]
    return named if named.isprintable() and named else auth.user_id or DEFAULT_ACTOR


Actor = Annotated[str, Depends(actor_of)]


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
        provider: str, body: ProviderBody, actor: Actor
    ) -> dict[str, object]:
        view = await _guarded(
            service().set_provider_concurrency(actor, provider, body.concurrency_limit)
        )
        return view.as_json()

    @router.post("/login/start")
    async def login_start(body: LoginStartBody, actor: Actor) -> dict[str, str]:
        login = await _guarded(service().login_start(actor, body.provider))
        return {
            "device_auth_id": login.device_auth_id,
            "user_code": login.user_code,
            "verify_url": login.verify_url,
        }

    @router.post("/login/poll")
    async def login_poll(body: LoginPollBody, actor: Actor) -> dict[str, object]:
        result = await _guarded(
            service().login_poll(
                actor,
                body.provider,
                body.device_auth_id,
                body.user_code,
                body.target(),
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
        subscription_id: str, body: SettingsBody, actor: Actor
    ) -> dict[str, object]:
        changes = {name: getattr(body, name) for name in body.model_fields_set}
        view = await _guarded(
            service().update_settings(actor, subscription_id, changes)
        )
        return view.as_json()

    @router.put("/{subscription_id}/enabled")
    async def set_enabled(
        subscription_id: str, body: EnabledBody, actor: Actor
    ) -> dict[str, object]:
        view = await _guarded(
            service().set_enabled(actor, subscription_id, body.enabled)
        )
        return view.as_json()

    @router.post("/{subscription_id}/refresh-limits")
    async def refresh_limits(subscription_id: str, actor: Actor) -> dict[str, object]:
        result = await _guarded(service().refresh_limits(actor, subscription_id))
        return {
            "refreshed": result.refreshed,
            "status": result.status,
            "subscription": result.subscription.as_json(),
        }

    @router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_subscription(subscription_id: str, actor: Actor) -> None:
        await _guarded(service().remove(actor, subscription_id))

    return router


async def _guarded(work: Awaitable[T]) -> T:
    try:
        return await work
    except (AdminError, ProviderLoginError) as error:
        raise HTTPException(_status_of(error), detail=str(error)) from error


def _status_of(error: Exception) -> int:
    return next(code for kind, code in STATUS_BY_ERROR if isinstance(error, kind))
