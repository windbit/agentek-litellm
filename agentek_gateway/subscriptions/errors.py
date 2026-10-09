import httpx

import litellm

INTERNAL_ATTRIBUTE = "agentek_internal"
NO_CAPACITY_STATUS = 429
RETRY_AFTER_HEADER = "retry-after"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


class NoAvailableSubscriptionsError(litellm.NotFoundError):
    """Final for the router retry loop (NotFoundError) yet reported to the client as 429 with Retry-After."""

    def __init__(
        self, model: str, recovery_in_s: float | None, retry_after_s: int
    ) -> None:
        text = no_capacity_message(recovery_in_s)
        response = httpx.Response(
            NO_CAPACITY_STATUS, request=httpx.Request("POST", "http://agentek")
        )
        super().__init__(
            message=text, model=model, llm_provider="agentek", response=response
        )
        self.message = text
        self.status_code = NO_CAPACITY_STATUS
        self.headers = {RETRY_AFTER_HEADER: str(retry_after_s)}
        mark_internal(self)


def mark_internal(error: BaseException) -> None:
    setattr(error, INTERNAL_ATTRIBUTE, True)


def is_internal_error(error: BaseException) -> bool:
    return getattr(error, INTERNAL_ATTRIBUTE, False) is True


def no_capacity_message(recovery_in_s: float | None) -> str:
    if recovery_in_s is None:
        return "No working subscriptions are available"
    return f"All subscriptions are exhausted, capacity returns in about {_humanize(recovery_in_s)}"


def retry_after_for_upstream(
    status_code: int, headers: dict[str, str], retry_after_s: int
) -> dict[str, str]:
    if status_code != NO_CAPACITY_STATUS:
        return {}
    if any(name.lower() == RETRY_AFTER_HEADER for name in headers):
        return {}
    return {RETRY_AFTER_HEADER: str(retry_after_s)}


def _humanize(seconds: float) -> str:
    if seconds >= SECONDS_PER_HOUR:
        hours = round(seconds / SECONDS_PER_HOUR)
        return f"{hours} h"
    minutes = max(1, round(seconds / SECONDS_PER_MINUTE))
    return f"{minutes} min"
