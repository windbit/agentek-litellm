from collections.abc import Awaitable
from typing import TypeVar

from litellm._logging import verbose_proxy_logger

T = TypeVar("T")


async def guarded(
    hook: str,
    work: Awaitable[T],
    fallback: T,
    reraise: tuple[type[BaseException], ...] = (),
) -> T:
    """Runs plugin work so that its failure is logged in full and never reaches the request."""
    try:
        return await work
    except reraise:
        raise
    except Exception:  # noqa: BLE001
        verbose_proxy_logger.exception("agentek_gateway %s failed", hook)
        return fallback
