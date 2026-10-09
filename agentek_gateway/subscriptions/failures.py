from dataclasses import dataclass

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .compat import StrEnum, assert_never
from .config import GatewayConfig
from .events import (
    AccountDeactivated,
    LimitExhausted,
    LimitsObserved,
    Unauthorized,
)
from .machine import ImplausibleReset
from .model import Limits, Subscription, UsageRecord
from .ports import StateStore
from .providers.base import (
    AccountBanned,
    AuthRejected,
    ErrorClass,
    Headers,
    LimitReached,
    ModelNotSupported,
    RequestRejected,
    SubscriptionProvider,
    Unclassified,
)
from .providers.redact import redact_for_log
from .service import StateService
from .signals import SignalProcessor
from .slots import Reservation


class SwitchReason(StrEnum):
    LIMIT = "limit"
    AUTH = "auth"
    BANNED = "banned"
    OVERLOADED = "overloaded"
    UNSUPPORTED = "unsupported"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class FailedAttempt:
    subscription: Subscription
    reservation: Reservation
    status: int
    headers: Headers
    body: str


class FailureRouter:
    """Turns one classified upstream failure into state changes, the negative model cache and a switch reason."""

    def __init__(
        self,
        states: StateService,
        signals: SignalProcessor,
        store: StateStore,
        clock: Clock,
        config: GatewayConfig,
    ) -> None:
        self._states = states
        self._signals = signals
        self._store = store
        self._clock = clock
        self._config = config

    async def handle(
        self, provider: SubscriptionProvider, attempt: FailedAttempt, error: ErrorClass
    ) -> SwitchReason | None:
        await self._record_limits(provider, attempt)
        subscription = attempt.subscription
        match error:
            case LimitReached(window=window, reset_at=reset_at):
                applied = await self._states.apply(
                    subscription, LimitExhausted(window, reset_at)
                )
                for note in applied.notes:
                    self._log_implausible(subscription, note)
                return SwitchReason.LIMIT
            case AuthRejected():
                await self._states.apply(subscription, Unauthorized())
                return SwitchReason.AUTH
            case AccountBanned():
                await self._states.apply(subscription, AccountDeactivated())
                return SwitchReason.BANNED
            case ModelNotSupported():
                await self._store.mark_model_unsupported(
                    subscription.id,
                    attempt.reservation.model_group,
                    self._config.tuning_for(
                        subscription.provider
                    ).unsupported_model_ttl_s,
                )
                return SwitchReason.UNSUPPORTED
            case RequestRejected():
                return None
            case Unclassified(immediate=immediate, recognized=recognized):
                if not recognized:
                    verbose_proxy_logger.warning(
                        "agentek_gateway unrecognized provider response %s",
                        redact_for_log(
                            subscription.name,
                            attempt.status,
                            attempt.headers,
                            attempt.body,
                        ),
                    )
                await self._signals.on_unclassified_error(
                    subscription, immediate=immediate
                )
                return SwitchReason.OVERLOADED
            case _:
                assert_never(error)

    async def observe_limits(
        self,
        provider: SubscriptionProvider,
        subscription: Subscription,
        headers: Headers,
    ) -> None:
        limits = await self._write_usage(provider, subscription, headers)
        if limits:
            await self._states.apply(subscription, LimitsObserved(limits))

    async def _record_limits(
        self, provider: SubscriptionProvider, attempt: FailedAttempt
    ) -> None:
        await self._write_usage(provider, attempt.subscription, attempt.headers)

    async def _write_usage(
        self,
        provider: SubscriptionProvider,
        subscription: Subscription,
        headers: Headers,
    ) -> Limits | None:
        now = self._clock.now()
        limits = provider.parse_limits(headers, None, now=now)
        if not limits or not (limits.five_hour or limits.weekly):
            return None
        await self._store.write_usage(subscription.id, UsageRecord(limits, now))
        return limits

    @staticmethod
    def _log_implausible(subscription: Subscription, note: ImplausibleReset) -> None:
        verbose_proxy_logger.warning(
            "agentek_gateway implausible reset time %s from subscription %s",
            note.reset_at,
            subscription.name,
        )
