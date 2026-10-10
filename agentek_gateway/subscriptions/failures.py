from dataclasses import dataclass

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .compat import StrEnum, assert_never
from .config import GatewayConfig
from .events import (
    Event,
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
from .service import TRANSIENT_STORE_ERRORS, StateService
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
    """Window readings repeat on nearly every response: one is written when it changed (at most every few seconds) or went stale."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._last: dict[SubscriptionId, tuple[float, Limits]] = {}

    def claim(self, subscription_id: SubscriptionId, limits: Limits) -> bool:
        """True when this reading is to be written; the claim is taken before the write, so concurrent readings write once."""
        last = self._last.get(subscription_id)
        now = self._clock.now()
        if last is not None:
            wait_s = (
                USAGE_WRITE_REFRESH_S
                if limits == last[1]
                else USAGE_WRITE_MIN_INTERVAL_S
            )
            if now - last[0] < wait_s:
                return False
        self._last[subscription_id] = (now, limits)
        return True

    def forget(self, subscription_id: SubscriptionId) -> None:
        self._last.pop(subscription_id, None)


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
        event = state_event_of(error)
        if event is not None and not isinstance(error, AuthRejected):
            self._states.hold(attempt.subscription, event)

    async def handle(
        self, provider: SubscriptionProvider, attempt: FailedAttempt, error: ErrorClass
    ) -> SwitchReason | None:
        """The state change goes first and is queued for retry when Redis is down; usage windows are written after it."""
        try:
            return await self._handle_error(attempt, error)
        finally:
            await self._record_limits(provider, attempt)

    async def _handle_error(
        self, attempt: FailedAttempt, error: ErrorClass
    ) -> SwitchReason | None:
        subscription = attempt.subscription
        event = state_event_of(error)
        if isinstance(error, AuthRejected) and await self._recently_refreshed(
            subscription
        ):
            event = None
        if event is not None:
            applied = await self._states.record(subscription, event)
            for note in applied.notes:
                self._log_implausible(subscription, note)
        match error:
            case LimitReached():
                return SwitchReason.LIMIT
            case AuthRejected():
                return SwitchReason.AUTH
            case AccountBanned():
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
        """Window readings of a response; a failed write is logged and never fails the request."""
        limits = self._read_limits(provider, headers)
        if limits and self._usage_gate.claim(subscription.id, limits):
            await self._write_usage(subscription.id, limits)
        return limits

    async def _record_limits(
        self, provider: SubscriptionProvider, attempt: FailedAttempt
    ) -> None:
        limits = self._read_limits(provider, attempt.headers)
        if limits:
            await self._write_usage(attempt.subscription.id, limits)

    async def _recently_refreshed(self, subscription: Subscription) -> bool:
        try:
            return await self._store.recently_refreshed(subscription.credential_name)
        except TRANSIENT_STORE_ERRORS:
            return False

    def _read_limits(
        self, provider: SubscriptionProvider, headers: Headers
    ) -> Limits | None:
        limits = provider.parse_limits(headers, None, now=self._clock.now())
        return limits if limits and (limits.five_hour or limits.weekly) else None

    async def _write_usage(
        self, subscription_id: SubscriptionId, limits: Limits
    ) -> None:
        try:
            await self._store.write_usage(
                subscription_id, UsageRecord(limits, self._clock.now())
            )
        except TRANSIENT_STORE_ERRORS as error:
            self._usage_gate.forget(subscription_id)
            verbose_proxy_logger.warning(
                "agentek_gateway usage windows of %s were not written (%s)",
                subscription_id,
                type(error).__name__,
            )

    @staticmethod
    def _log_implausible(subscription: Subscription, note: ImplausibleReset) -> None:
        verbose_proxy_logger.warning(
            "agentek_gateway implausible reset time %s from subscription %s",
            note.reset_at,
            subscription.name,
        )


def state_event_of(error: ErrorClass) -> Event | None:
    match error:
        case LimitReached(window=window, reset_at=reset_at):
            return LimitExhausted(window, reset_at)
        case AuthRejected():
            return Unauthorized()
        case AccountBanned():
            return AccountDeactivated()
        case _:
            return None
