"""
Internal request context for LiteLLM.

Provides a ContextVar-based mechanism for internal signals that must not
be settable from user input. Context variables are scoped to the current
asyncio task and cannot be injected via HTTP request bodies.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

# When True, suppresses async logging and billing for internal sub-calls
# (e.g., emulated file-search steps that make nested LLM calls).
is_internal_call: ContextVar[bool] = ContextVar("is_internal_call", default=False)


@contextmanager
def internal_call_scope() -> Iterator[None]:
    """Marks calls made within the block as internal sub-calls."""
    token = is_internal_call.set(True)
    try:
        yield
    finally:
        is_internal_call.reset(token)
