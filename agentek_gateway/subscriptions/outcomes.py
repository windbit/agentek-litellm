import asyncio
from collections.abc import AsyncIterator, Mapping

from .attempts import read_request_id
from .errors import is_internal_error, retry_after_for_upstream
from .failures import FailedAttempt
from .gateway import GatewayParts, call_id_of, deployment_id_of, request_key_of
from .guard import guarded
from .model import Route, Subscription
from .providers.base import ErrorClass, SubscriptionProvider, Unclassified
from .providers.observer import ObservedFailure
from .registry import UpstreamReply
from .slots import Reservation

ADDITIONAL_HEADERS_KEY = "additional_headers"
STREAM_FAILURE_TYPES = frozenset({"error", "response.failed"})
UNKNOWN_STATUS = 0
SLOT_REFRESHES_PER_TTL = 3


class OutcomeTracker:
    """What happened to an attempt: success, each failure source exactly once, stream end, final cleanup."""

    def __init__(self, parts: GatewayParts) -> None:
        self._parts = parts

    def on_observed(self, failure: ObservedFailure) -> None:
        """Sink of the provider error observer; sync because get_error_class is sync."""
        context = failure.context
        key = (context.request_id, context.deployment_id)
        if self._parts.registry.was_handled(key):
            return
        self._parts.registry.mark_handled(key)
        self._parts.registry.remember_upstream(
            context.request_id, UpstreamReply(failure.status, dict(failure.headers))
        )
        self._parts.tasks.spawn(self._handle_observed(failure))

    async def on_success(self, kwargs: Mapping[str, object], response: object) -> None:
        request_id, reservation = self._attempt_of(kwargs)
        try:
            subscription = self._subscription(reservation)
            if reservation and subscription:
                provider = self._provider(subscription)
                limits = (
                    provider.parse_limits(
                        _additional_headers(response), None, now=self._parts.clock.now()
                    )
                    if provider
                    else None
                )
                await self._parts.signals.on_success(subscription, limits)
        finally:
            await self.finish(request_id)

    async def release_slots(self, request: Mapping[str, object]) -> None:
        """Frees the slots of a finished non-streaming request without waiting for the success log event."""
        await self._parts.ledger.release_request(request_key_of(request))

    async def on_failure_event(self, kwargs: Mapping[str, object]) -> None:
        """Failures the provider observer cannot see: no HTTP reply (connection, timeout), not an internal error."""
        error = kwargs.get("exception")
        if isinstance(error, BaseException) and is_internal_error(error):
            return
        request_id, reservation = self._attempt_of(kwargs)
        subscription = self._subscription(reservation)
        if not (request_id and reservation and subscription):
            return
        key = (request_id, reservation.deployment_id)
        registry = self._parts.registry
        if registry.was_handled(key) or registry.is_streaming(key):
            return
        registry.mark_handled(key)
        await self._apply(
            subscription,
            reservation,
            self._classify(subscription, error),
            _no_reply(error),
        )

    async def on_headers(
        self, data: Mapping[str, object], response: object
    ) -> dict[str, str] | None:
        if response is None:
            return self._retry_after(data)
        reservation = self._parts.ledger.active(request_key_of(data))
        subscription = self._subscription(reservation)
        provider = self._provider(subscription) if subscription else None
        headers = _additional_headers(response)
        if subscription and provider and headers:
            await self._parts.failures.observe_limits(provider, subscription, headers)
        return None

    async def watch_stream(
        self, request_data: Mapping[str, object], response: AsyncIterator[object]
    ) -> AsyncIterator[object]:
        request_id = request_key_of(request_data)
        reservation = self._parts.ledger.active(request_id)
        subscription = self._subscription(reservation)
        if not (request_id and reservation and subscription):
            async for chunk in response:
                yield chunk
            return
        key = (request_id, reservation.deployment_id)
        self._parts.registry.mark_streaming(key)
        refresh_every_s = (
            self._parts.config.defaults.slot_ttl_s / SLOT_REFRESHES_PER_TTL
        )
        refreshed_at = self._parts.clock.now()
        try:
            async for chunk in response:
                if self._parts.clock.now() - refreshed_at >= refresh_every_s:
                    refreshed_at = self._parts.clock.now()
                    await guarded(
                        "slot refresh", self._parts.ledger.extend(reservation), False
                    )
                await guarded(
                    "stream chunk",
                    self._inspect_chunk(subscription, reservation, chunk),
                    None,
                )
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as error:
            await guarded(
                "stream error",
                self._handle_stream_exception(subscription, reservation, error),
                None,
            )
            raise
        finally:
            self._parts.tasks.spawn(self.finish(request_id))

    async def finish(self, request_id: str | None) -> None:
        if not request_id:
            return
        self._parts.attempts.finish(request_id)
        await self._parts.ledger.finish(request_id)

    async def _handle_observed(self, failure: ObservedFailure) -> None:
        context = failure.context
        reservation = self._parts.ledger.find(context.request_id, context.deployment_id)
        subscription = self._subscription(reservation)
        if reservation and subscription:
            attempt = FailedAttempt(
                subscription,
                reservation,
                failure.status,
                failure.headers,
                failure.body,
                self._egress_note(subscription),
            )
            await self._apply_attempt(attempt, failure.error)

    async def _inspect_chunk(
        self, subscription: Subscription, reservation: Reservation, chunk: object
    ) -> None:
        event = _failure_event(chunk)
        provider = self._provider(subscription)
        if event is None or provider is None:
            return
        error = provider.classify_stream_failure(event, now=self._parts.clock.now())
        await self._apply(subscription, reservation, error, _no_reply(None))

    async def _handle_stream_exception(
        self, subscription: Subscription, reservation: Reservation, error: Exception
    ) -> None:
        provider = self._provider(subscription)
        status = getattr(error, "status_code", None)
        if provider is None or not isinstance(status, int):
            return
        classified = provider.classify_error(
            status, {}, str(error), now=self._parts.clock.now()
        )
        if isinstance(classified, Unclassified):
            return
        await self._apply(subscription, reservation, classified, _no_reply(error))

    async def _apply(
        self,
        subscription: Subscription,
        reservation: Reservation,
        error: ErrorClass,
        reply: UpstreamReply,
    ) -> None:
        attempt = FailedAttempt(
            subscription,
            reservation,
            reply.status,
            reply.headers,
            "",
            self._egress_note(subscription),
        )
        await self._apply_attempt(attempt, error)

    async def _apply_attempt(self, attempt: FailedAttempt, error: ErrorClass) -> None:
        provider = self._provider(attempt.subscription)
        if provider is None:
            return
        reason = await self._parts.failures.handle(provider, attempt, error)
        if reason and attempt.reservation.alternatives > 0:
            self._parts.telemetry.switched(attempt.subscription, reason)
        await self._parts.ledger.release(attempt.reservation)

    def _retry_after(self, data: Mapping[str, object]) -> dict[str, str] | None:
        request_id = request_key_of(data)
        upstream = (
            self._parts.registry.take_upstream(request_id) if request_id else None
        )
        if upstream is None:
            return None
        added = retry_after_for_upstream(
            upstream.status,
            dict(upstream.headers),
            self._parts.config.defaults.no_capacity_retry_after_s,
        )
        return added or None

    def _attempt_of(
        self, kwargs: Mapping[str, object]
    ) -> tuple[str | None, Reservation | None]:
        sources = _sources(kwargs)
        request_id = next(
            (stamped for stamped in map(read_request_id, sources) if stamped),
            next((call_id for call_id in map(call_id_of, sources) if call_id), None),
        )
        deployment_id = next(filter(None, map(deployment_id_of, sources)), None)
        found = self._parts.ledger.find(request_id, deployment_id)
        return request_id, found or self._parts.ledger.active(request_id)

    def _egress_note(self, subscription: Subscription) -> str:
        return self._parts.egress.note(
            Route(subscription.provider, subscription.egress)
        )

    def _classify(self, subscription: Subscription, error: object) -> ErrorClass:
        provider = self._provider(subscription)
        status = getattr(error, "status_code", None)
        if provider is not None and isinstance(status, int):
            return provider.classify_error(
                status, {}, str(error), now=self._parts.clock.now()
            )
        return Unclassified(immediate=False, recognized=True)

    def _subscription(self, reservation: Reservation | None) -> Subscription | None:
        snapshot = self._parts.snapshot.current
        if reservation is None or snapshot is None:
            return None
        return snapshot.subscriptions.get(reservation.subscription_id)

    def _provider(self, subscription: Subscription) -> SubscriptionProvider | None:
        return self._parts.providers.get(subscription.provider)


def _sources(kwargs: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    params = kwargs.get("litellm_params")
    nested = (params,) if isinstance(params, Mapping) else ()
    return (kwargs, *nested)


def _additional_headers(response: object) -> Mapping[str, str]:
    hidden = getattr(response, "_hidden_params", None)
    headers = (
        hidden.get(ADDITIONAL_HEADERS_KEY) if isinstance(hidden, Mapping) else None
    )
    return headers if isinstance(headers, Mapping) else {}


def _no_reply(error: BaseException | None) -> UpstreamReply:
    status = getattr(error, "status_code", None)
    return UpstreamReply(status if isinstance(status, int) else UNKNOWN_STATUS, {})


def _failure_event(chunk: object) -> Mapping[str, object] | None:
    kind = (
        chunk.get("type")
        if isinstance(chunk, Mapping)
        else getattr(chunk, "type", None)
    )
    if kind not in STREAM_FAILURE_TYPES:
        return None
    if isinstance(chunk, Mapping):
        return chunk
    dump = getattr(chunk, "model_dump", None)
    dumped = dump() if callable(dump) else None
    return dumped if isinstance(dumped, Mapping) else None
