from collections.abc import AsyncGenerator, Mapping, Sequence
from datetime import datetime

from fastapi import HTTPException

from litellm._logging import verbose_proxy_logger
from litellm.caching.caching import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.types.utils import CallTypes, CallTypesLiteral

from .attempts import stamp_request_id
from .config import ProviderTuning
from .errors import NoAvailableSubscriptionsError
from .gateway import SubscriptionBusyError, drop_subscription_deployments
from .guard import guarded
from .runtime import GLOBAL_SLOT, RuntimeSlot

RESPONSES_CALL_TYPES = frozenset({"responses", "aresponses"})
DEFAULT_RETRY_AFTER_S = ProviderTuning().no_capacity_retry_after_s
PASSTHROUGH_ERRORS = (NoAvailableSubscriptionsError, SubscriptionBusyError)

Deployment = dict[str, object]
Fields = dict[str, object]


class SubscriptionCallback(CustomLogger):
    """LiteLLM hooks of the subscription pool, all defined here: LiteLLM detects overridden hooks per class."""

    def __init__(self, slot: RuntimeSlot | None = None) -> None:
        super().__init__()
        self._slot = slot or GLOBAL_SLOT

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: Fields,
        call_type: CallTypesLiteral,
    ) -> Fields:
        await guarded("pre_call", _prepare(data, call_type), None)
        runtime = self._slot.runtime
        if runtime is not None:
            data.update(runtime.gateway.retry_settings(data))
        return data

    async def async_filter_deployments(
        self,
        model: str,
        healthy_deployments: list[Deployment],
        messages: list[Fields] | None,
        request_kwargs: Fields | None = None,
        parent_otel_span: object | None = None,
    ) -> list[Deployment]:
        runtime = self._slot.runtime
        if runtime is None:
            return drop_subscription_deployments(
                model, healthy_deployments, DEFAULT_RETRY_AFTER_S
            )
        try:
            return await runtime.gateway.filter(
                model, healthy_deployments, request_kwargs or {}
            )
        except NoAvailableSubscriptionsError:
            raise
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway filter failed")
            return runtime.gateway.without_subscriptions(model, healthy_deployments)

    async def async_pre_call_deployment_hook(
        self, kwargs: Fields, call_type: CallTypes | None
    ) -> Fields | None:
        runtime = self._slot.runtime
        if runtime is None:
            return None
        return await guarded(
            "pre_call_deployment",
            runtime.gateway.before_attempt(kwargs),
            None,
            reraise=PASSTHROUGH_ERRORS,
        )

    async def async_log_success_event(
        self,
        kwargs: Fields,
        response_obj: object,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        runtime = self._slot.runtime
        if runtime is not None:
            await guarded(
                "log_success", runtime.outcomes.on_success(kwargs, response_obj), None
            )

    async def async_log_failure_event(
        self,
        kwargs: Fields,
        response_obj: object,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        runtime = self._slot.runtime
        if runtime is not None:
            await guarded(
                "log_failure", runtime.outcomes.on_failure_event(kwargs), None
            )

    async def async_post_call_success_hook(
        self,
        data: Fields,
        user_api_key_dict: UserAPIKeyAuth,
        response: object,
    ) -> object:
        runtime = self._slot.runtime
        if runtime is not None and not data.get("stream"):
            await guarded(
                "post_call_success", runtime.outcomes.release_slots(data), None
            )
        return None

    async def async_post_call_failure_hook(
        self,
        request_data: Fields,
        original_exception: Exception,
        user_api_key_dict: UserAPIKeyAuth,
        traceback_str: str | None = None,
    ) -> HTTPException | None:
        runtime = self._slot.runtime
        if runtime is not None:
            await guarded(
                "post_call_failure",
                runtime.outcomes.finish(runtime.request_key(request_data)),
                None,
            )
        return None

    async def async_post_call_response_headers_hook(
        self,
        data: Fields,
        user_api_key_dict: UserAPIKeyAuth,
        response: object,
        request_headers: Mapping[str, str] | None = None,
        litellm_call_info: Fields | None = None,
    ) -> dict[str, str] | None:
        runtime = self._slot.runtime
        if runtime is None:
            return None
        return await guarded(
            "response_headers", runtime.outcomes.on_headers(data, response), None
        )

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: AsyncGenerator[object, None],
        request_data: Fields,
    ) -> AsyncGenerator[object, None]:
        runtime = self._slot.runtime
        stream = (
            runtime.outcomes.watch_stream(request_data, response)
            if runtime is not None
            else response
        )
        async for chunk in stream:
            yield chunk


async def _prepare(data: Fields, call_type: CallTypesLiteral) -> None:
    """Only Responses requests carry litellm_metadata; on other paths a client-sent one would shadow the router's."""
    if call_type not in RESPONSES_CALL_TYPES:
        data.pop("litellm_metadata", None)
    stamp_request_id(data)


__all__: Sequence[str] = ("SubscriptionCallback",)
