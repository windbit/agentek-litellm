import contextlib
import contextvars
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from litellm._logging import verbose_proxy_logger

from .base import ErrorClass, ModelNotSupported

REWRITTEN_STATUS = 409
ORIGINAL_CLASS_ATTRIBUTE = "_agentek_original_get_error_class"


@dataclass(frozen=True, slots=True)
class AttemptContext:
    request_id: str
    attempt: int
    subscription_id: str
    deployment_id: str
    alternatives: int


@dataclass(frozen=True, slots=True)
class ObservedFailure:
    context: AttemptContext
    status: int
    headers: Mapping[str, str]
    body: str
    error: ErrorClass


current_attempt: contextvars.ContextVar[AttemptContext | None] = contextvars.ContextVar(
    "agentek_attempt", default=None
)


@contextlib.contextmanager
def attempt_scope(context: AttemptContext) -> Iterator[None]:
    token = current_attempt.set(context)
    try:
        yield
    finally:
        current_attempt.reset(token)


class FailureSink(Protocol):
    def __call__(self, failure: ObservedFailure) -> None: ...


Classifier = Callable[[int, Mapping[str, str], str], ErrorClass]


def status_for_client(error: ErrorClass, status: int, context: AttemptContext) -> int:
    """409 makes the router retry on another subscription; the last candidate keeps the original status."""
    if isinstance(error, ModelNotSupported) and context.alternatives > 0:
        return REWRITTEN_STATUS
    return status


def wrap_get_error_class(
    original: Callable[..., object], classify: Classifier, sink: FailureSink
) -> Callable[..., object]:
    def wrapped(
        self: object, error_message: str, status_code: int, headers: Mapping[str, str]
    ) -> object:
        context = current_attempt.get()
        if context is None:
            return original(self, error_message, status_code, headers)
        rewritten = status_code
        try:
            error = classify(status_code, headers, error_message)
            sink(
                ObservedFailure(
                    context, status_code, dict(headers), error_message, error
                )
            )
            rewritten = status_for_client(error, status_code, context)
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway error observer failed")
        return original(self, error_message, rewritten, headers)

    return wrapped


def install_error_observer(
    provider_class: type, classify: Classifier, sink: FailureSink
) -> bool:
    if hasattr(provider_class, ORIGINAL_CLASS_ATTRIBUTE):
        return False
    original = provider_class.get_error_class
    setattr(provider_class, ORIGINAL_CLASS_ATTRIBUTE, original)
    provider_class.get_error_class = wrap_get_error_class(original, classify, sink)
    return True


def uninstall_error_observer(provider_class: type) -> None:
    original = getattr(provider_class, ORIGINAL_CLASS_ATTRIBUTE, None)
    if original is None:
        return
    provider_class.get_error_class = original
    delattr(provider_class, ORIGINAL_CLASS_ATTRIBUTE)
