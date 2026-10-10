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
from .model import Limits, Subscription, SubscriptionId, UsageRecord
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

USAGE_WRITE_MIN_INTERVAL_S = 2.0
USAGE_WRITE_REFRESH_S = 30.0


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
    egress_note: str = ""


class UsageWriteGate:
    """Window readings repeat on nearly every response; a reading is written when it changed (not more than every few seconds) or went stale."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._last: dict[SubscriptionId, tuple[float, Limits]] = {}

    def due(self, subscription_id: SubscriptionId, limits: Limits) -> bool:
        last = self._last.get(subscription_id)
        if last is None:
            return True
        age_s = self._clock.now() - last[0]
        if limits == last[1]:
            return age_s >= USAGE_WRITE_REFRESH_S
        return age_s >= USAGE_WRITE_MIN_INTERVAL_S

    def written(self, subscription_id: SubscriptionId, limits: Limits) -> None:
        self._last[subscription_id] = (self._clock.now(), limits)


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
        self._usage_gate = UsageWriteGate(clock)

    def hold_now(self, attempt: FailedAttempt, error: ErrorClass) -> None:
        """A failure that blocks the subscription is known to this process before anything is awaited."""
        subscription = attempt.subscription
        match error:
            case LimitReached(window=window, reset_at=reset_at):
                self._states.hold(subscription, LimitExhausted(window, reset_at))
            case AccountBanned():
                self._states.hold(subscription, AccountDeactivated())
            case _:
                return

    async def handle(
        self, provider: SubscriptionProvider, attempt: FailedAttempt, error: ErrorClass
    ) -> SwitchReason | None:
        await self._record_limits(provider, attempt)
        subscription = attempt.subscription
        match error:
            case LimitReached(window=window, reset_at=reset_at):
                applied = await self._states.record(
                    subscription, LimitExhausted(window, reset_at)
                )
                for note in applied.notes:
                    self._log_implausible(subscription, note)
                return SwitchReason.LIMIT
            case AuthRejected():
                if not await self._store.recently_refreshed(
                    subscription.credential_name
                ):
                    await self._states.record(subscription, Unauthorized())
                return SwitchReason.AUTH
            case AccountBanned():
                await self._states.record(subscription, AccountDeactivated())
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
                verbose_proxy_logger.warning(
                    "agentek_gateway unclassified error subscription=%s status=%s %s",
                    subscription.name,
                    attempt.status,
                    attempt.egress_note,
                )
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
        limits = await self.record_usage(provider, subscription, headers)
        if limits:
            await self._states.observe(subscription, LimitsObserved(limits))

    async def record_usage(
        self,
        provider: SubscriptionProvider,
        subscription: Subscription,
        headers: Headers,
    ) -> Limits | None:
        limits = self._read_limits(provider, headers)
        if limits and self._usage_gate.due(subscription.id, limits):
            await self._write_usage(subscription.id, limits)
        return limits

    async def _record_limits(
        self, provider: SubscriptionProvider, attempt: FailedAttempt
    ) -> None:
        limits = self._read_limits(provider, attempt.headers)
        if limits:
            await self._write_usage(attempt.subscription.id, limits)

    def _read_limits(
        self, provider: SubscriptionProvider, headers: Headers
    ) -> Limits | None:
        limits = provider.parse_limits(headers, None, now=self._clock.now())
        return limits if limits and (limits.five_hour or limits.weekly) else None

    async def _write_usage(
        self, subscription_id: SubscriptionId, limits: Limits
    ) -> None:
        await self._store.write_usage(
            subscription_id, UsageRecord(limits, self._clock.now())
        )
        self._usage_gate.written(subscription_id, limits)

    @staticmethod
    def _log_implausible(subscription: Subscription, note: ImplausibleReset) -> None:
        verbose_proxy_logger.warning(
            "agentek_gateway implausible reset time %s from subscription %s",
            note.reset_at,
            subscription.name,
        )
