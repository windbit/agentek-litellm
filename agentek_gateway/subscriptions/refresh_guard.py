import time
from collections.abc import Callable

from litellm.llms.chatgpt import authenticator
from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

from .credential_pairs import CredentialPairs
from .providers.chatgpt import ChatgptAuth, jwt_expiry
from .token_coordination import (
    RECENT_REFRESH_WINDOW_S,
    LatestAuth,
    SyncTokenCoordinator,
)

LOCK_TIMEOUT_STATUS = 503
TOKEN_EXPIRY_SKEW_S = 60


class RefreshGuard:
    """Serializes the authenticator's mid-request refresh with the leader's: one provider call, newest pair shared."""

    def __init__(
        self,
        coordinator: SyncTokenCoordinator,
        stored_pairs: CredentialPairs,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._coordinator = coordinator
        self._stored_pairs = stored_pairs
        self._now = now

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
        """The shared pair first, then the database copy: Redis only speeds this up, the database decides."""
        latest = self._coordinator.read_latest(credential_name)
        for candidate in (
            latest.auth if latest else None,
            self._stored_pairs.read(credential_name),
        ):
            if candidate is not None and self._supersedes(
                candidate, stale_refresh_token
            ):
                return {
                    "access_token": candidate.access_token,
                    "refresh_token": candidate.refresh_token,
                    "id_token": candidate.id_token or "",
                }
        return None

    def _supersedes(self, candidate: ChatgptAuth, stale_refresh_token: str) -> bool:
        if candidate.refresh_token != stale_refresh_token:
            return True
        expires_at = candidate.expires_at
        return expires_at is not None and expires_at - self._now() > TOKEN_EXPIRY_SKEW_S


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
