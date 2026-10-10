from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import litellm
from litellm._logging import verbose_proxy_logger

from .attempts import AttemptTracker, read_request_id, request_metadata
from .clock import Clock
from .config import GatewayConfig
from .egress import EgressBook
from .errors import NoAvailableSubscriptionsError, mark_internal
from .expiring import ExpiringMap
from .failures import FailureRouter, SwitchReason
from .filtering import Deployment, FilterContext, filter_deployments
from .model import Subscription
from .providers.base import SubscriptionProvider
from .providers.observer import AttemptContext, current_attempt
from .registry import AttemptRegistry
from .selection import Snapshot, subscription_of
from .signals import SignalProcessor
from .slots import Reservation, ReserveRequest, SlotLedger
from .snapshot import SnapshotCache
from .stickiness import StickyBook, prompt_cache_key_of, with_session_id
from .tasks import BackgroundTasks
from .telemetry import Telemetry

CREDENTIAL_TAG_PREFIX = "Credential: "
BUSY_STATUS = 409
SUBSCRIPTION_ID_PREFIX = "sub:"
MIN_POOL_RETRIES = 4
MAX_POOL_RETRIES = 32


@dataclass(frozen=True, slots=True)
class Offer:
    """What the filter put forward for a request; it becomes an attempt only if the router really uses it."""

    deployment_id: str
    alternatives: int


@dataclass(frozen=True, slots=True)
class GatewayParts:
    clock: Clock
    config: GatewayConfig
    snapshot: SnapshotCache
    attempts: AttemptTracker
    ledger: SlotLedger
    sticky: StickyBook
    providers: Mapping[str, SubscriptionProvider]
    failures: FailureRouter
    signals: SignalProcessor
    registry: AttemptRegistry
    telemetry: Telemetry
    tasks: BackgroundTasks
    offers: ExpiringMap[str, Offer]
    egress: EgressBook = field(default_factory=EgressBook)


class SubscriptionBusyError(litellm.APIError):
    """Retried by the router on another deployment without cooling this one down."""

    def __init__(self, model: str) -> None:
        super().__init__(
            status_code=BUSY_STATUS,
            message="Subscription has no free slot",
            llm_provider="agentek",
            model=model,
        )
        mark_internal(self)


def deployment_id_of(request: Mapping[str, object]) -> str | None:
    model_info = request_metadata(request).get("model_info")
    value = model_info.get("id") if isinstance(model_info, Mapping) else None
    return str(value) if value else None


def request_key_of(request: Mapping[str, object]) -> str | None:
    return read_request_id(request) or call_id_of(request)


def call_id_of(request: Mapping[str, object]) -> str | None:
    value = request.get("litellm_call_id")
    return value if isinstance(value, str) and value else None


class SubscriptionGateway:
    """Attempt lifecycle: which deployment a request may use and what is reserved for it."""

    def __init__(self, parts: GatewayParts) -> None:
        self._parts = parts

    def retry_settings(self, request: Mapping[str, object]) -> dict[str, object]:
        """Router retry settings a subscription model needs; empty for any other model.

        Only a rate-limit error (an exhausted subscription) retries up to the pool size;
        other errors keep the router's own count, which a client may lower or raise within the pool bounds.
        """
        model = str(request.get("model"))
        snapshot = self._parts.snapshot.current
        if snapshot is None or model not in snapshot.subscription_models:
            return {}
        enabled = sum(1 for sub in snapshot.subscriptions.values() if sub.enabled)
        pool = min(MAX_POOL_RETRIES, max(MIN_POOL_RETRIES, enabled))
        policies = request.get("model_group_retry_policy")
        merged = dict(policies) if isinstance(policies, Mapping) else {}
        own = merged.get(model)
        merged[model] = {
            **(own if isinstance(own, Mapping) else {}),
            "RateLimitErrorRetries": pool,
        }
        settings: dict[str, object] = {"model_group_retry_policy": merged}
        requested = request.get("num_retries")
        if isinstance(requested, int) and not isinstance(requested, bool):
            settings["num_retries"] = max(0, min(requested, MAX_POOL_RETRIES))
        return settings

    async def filter(
        self,
        model: str,
        deployments: Sequence[Deployment],
        request_kwargs: Mapping[str, object],
    ) -> list[Deployment]:
        parts = self._parts
        snapshot = parts.snapshot.current
        if snapshot is None:
            return self.without_subscriptions(model, deployments)
        request_id = read_request_id(request_kwargs)
        if parts.attempts.age_s(request_id) > parts.config.defaults.retry_budget_s:
            raise NoAvailableSubscriptionsError(
                model, None, parts.config.defaults.no_capacity_retry_after_s
            )
        cache_key = prompt_cache_key_of(request_kwargs)
        sticky_id = await parts.sticky.lookup(cache_key) if cache_key else None
        result = filter_deployments(
            model,
            deployments,
            snapshot,
            FilterContext(
                request_kwargs=request_kwargs,
                attempted=parts.attempts.attempted(request_id),
                sticky_subscription_id=sticky_id,
                now=parts.clock.now(),
                retry_after_s=parts.config.defaults.no_capacity_retry_after_s,
                in_flight_here=parts.ledger.in_flight_here(),
            ),
        )
        chosen = result.chosen
        if request_id and chosen and chosen.subscription_id:
            parts.offers.put(
                request_id, Offer(chosen.deployment_id, result.alternatives)
            )
        return result.deployments

    def without_subscriptions(
        self, model: str, deployments: Sequence[Deployment]
    ) -> list[Deployment]:
        snapshot = self._parts.snapshot.current
        return drop_subscription_deployments(
            model,
            deployments,
            self._parts.config.defaults.no_capacity_retry_after_s,
            snapshot,
        )

    async def before_attempt(
        self, kwargs: dict[str, object]
    ) -> dict[str, object] | None:
        """Reserves the slot for the chosen subscription deployment and binds the attempt for the observer."""
        parts = self._parts
        snapshot = parts.snapshot.current
        subscription = self._subscription_of_call(kwargs, snapshot)
        if snapshot is None or subscription is None:
            current_attempt.set(None)
            return None
        await self._note_attempt(kwargs, subscription)
        try:
            reservation = await self._reserve(kwargs, subscription)
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway slot reservation failed")
            current_attempt.set(None)
            raise SubscriptionBusyError(str(kwargs.get("model"))) from None
        if reservation is None:
            parts.telemetry.switched(subscription, SwitchReason.BUSY)
            raise SubscriptionBusyError(str(kwargs.get("model")))
        current_attempt.set(
            AttemptContext(
                request_id=reservation.request_id,
                attempt=reservation.attempt,
                subscription_id=subscription.id,
                deployment_id=reservation.deployment_id,
                alternatives=reservation.alternatives,
            )
        )
        _retag_credential(kwargs, subscription)
        return with_session_id(kwargs)

    async def _note_attempt(
        self, kwargs: Mapping[str, object], subscription: Subscription
    ) -> None:
        """Records the deployment the router actually took, and ties the chat to it."""
        parts = self._parts
        request_id = read_request_id(kwargs)
        deployment_id = deployment_id_of(kwargs)
        if request_id and deployment_id:
            offer = parts.offers.get(request_id)
            alternatives = (
                offer.alternatives
                if offer and offer.deployment_id == deployment_id
                else 0
            )
            parts.attempts.record(request_id, deployment_id, alternatives)
        cache_key = prompt_cache_key_of(kwargs)
        if cache_key:
            await parts.sticky.bind(cache_key, subscription.id)

    async def _reserve(
        self, kwargs: dict[str, object], subscription: Subscription
    ) -> Reservation | None:
        parts = self._parts
        request_id = request_key_of(kwargs)
        deployment_id = deployment_id_of(kwargs)
        if not request_id or not deployment_id:
            return None
        tuning = parts.config.tuning_for(subscription.provider)
        limit = subscription.concurrency_limit or tuning.concurrency_limit
        return await parts.ledger.reserve(
            ReserveRequest(
                request_id=request_id,
                deployment_id=deployment_id,
                subscription=subscription,
                model_group=_model_group(kwargs),
                alternatives=parts.attempts.alternatives(request_id),
                limit=limit,
            )
        )

    def _subscription_of_call(
        self, kwargs: Mapping[str, object], snapshot: Snapshot | None
    ) -> Subscription | None:
        deployment_id = deployment_id_of(kwargs)
        if snapshot is None or not deployment_id:
            return None
        subscription_id = subscription_of(deployment_id, kwargs, snapshot)
        return snapshot.subscriptions.get(subscription_id) if subscription_id else None


def drop_subscription_deployments(
    model: str,
    deployments: Sequence[Deployment],
    retry_after_s: int,
    snapshot: Snapshot | None = None,
) -> list[Deployment]:
    """What is left when subscriptions cannot be chosen: the plugin's id prefix or a known credential marks one."""
    remaining = [
        deployment
        for deployment in deployments
        if not _looks_like_subscription(deployment, snapshot)
    ]
    if not remaining:
        raise NoAvailableSubscriptionsError(model, None, retry_after_s)
    return remaining


def _looks_like_subscription(deployment: Deployment, snapshot: Snapshot | None) -> bool:
    model_info = deployment.get("model_info")
    deployment_id = model_info.get("id") if isinstance(model_info, Mapping) else None
    if isinstance(deployment_id, str) and deployment_id.startswith(
        SUBSCRIPTION_ID_PREFIX
    ):
        return True
    return snapshot is not None and (
        subscription_of("", deployment.get("litellm_params"), snapshot) is not None
    )


def _model_group(kwargs: Mapping[str, object]) -> str:
    group = request_metadata(kwargs).get("model_group")
    return str(group) if group else str(kwargs.get("model"))


def _retag_credential(kwargs: dict[str, object], subscription: Subscription) -> None:
    container = request_metadata(kwargs)
    if not isinstance(container, dict):
        return
    tags = container.get("tags")
    kept = (
        [
            tag
            for tag in tags
            if not (isinstance(tag, str) and tag.startswith(CREDENTIAL_TAG_PREFIX))
        ]
        if isinstance(tags, list)
        else []
    )
    container["tags"] = [
        *kept,
        f"{CREDENTIAL_TAG_PREFIX}{subscription.credential_name}",
    ]
