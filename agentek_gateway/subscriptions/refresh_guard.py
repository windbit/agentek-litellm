from collections.abc import Callable

from litellm.llms.chatgpt import authenticator
from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

from .providers.chatgpt import ChatgptAuth, jwt_expiry
from .token_coordination import (
    RECENT_REFRESH_WINDOW_S,
    LatestAuth,
    SyncTokenCoordinator,
)

LOCK_TIMEOUT_STATUS = 503


class RefreshGuard:
    """Serializes the authenticator's mid-request refresh with the leader's: one provider call, newest pair shared."""

    def __init__(self, coordinator: SyncTokenCoordinator) -> None:
        self._coordinator = coordinator

    def __call__(
        self,
        credential_name: str,
        stale_refresh_token: str,
        refresh: Callable[[], dict[str, str]],
    ) -> dict[str, str]:
        newer = self._newer(credential_name, stale_refresh_token)
        if newer is not None:
            return newer
        lock = self._coordinator.acquire(credential_name)
        if lock is None:
            raise RefreshAccessTokenError(
                message="Timed out waiting for another refresh of this credential",
                status_code=LOCK_TIMEOUT_STATUS,
            )
        try:
            newer = self._newer(credential_name, stale_refresh_token)
            if newer is not None:
                return newer
            tokens = refresh()
            self._coordinator.save_latest(credential_name, LatestAuth(_auth_of(tokens)))
            self._coordinator.mark_refreshed(credential_name, RECENT_REFRESH_WINDOW_S)
            return tokens
        finally:
            self._coordinator.release(credential_name, lock)

    def _newer(
        self, credential_name: str, stale_refresh_token: str
    ) -> dict[str, str] | None:
        latest = self._coordinator.read_latest(credential_name)
        if latest is None:
            return None
        same_pair = latest.auth.refresh_token == stale_refresh_token
        if same_pair and not self._coordinator.recently_refreshed(credential_name):
            return None
        return {
            "access_token": latest.auth.access_token,
            "refresh_token": latest.auth.refresh_token,
            "id_token": latest.auth.id_token or "",
        }


def install_refresh_guard(guard: RefreshGuard) -> None:
    authenticator.REFRESH_GUARD = guard


def uninstall_refresh_guard() -> None:
    authenticator.REFRESH_GUARD = None


def _auth_of(tokens: dict[str, str]) -> ChatgptAuth:
    access_token = tokens["access_token"]
    return ChatgptAuth(
        access_token=access_token,
        refresh_token=tokens["refresh_token"],
        id_token=tokens.get("id_token") or None,
        expires_at=jwt_expiry(access_token),
    )
