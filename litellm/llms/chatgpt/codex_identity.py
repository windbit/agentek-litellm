"""
Outbound client identity of the ChatGPT provider: the headers of the current Codex TUI release.

Under capacity pressure chatgpt.com/backend-api/codex sheds requests by client identity,
unknown clients and stale versions first (HTTP 200 + server_is_overloaded in the stream).
Observed and handled the same way by sub2api:
https://github.com/Wei-Shaw/sub2api/blob/98d86915becae9fe9491a91ffc6defd5235c8d2b/backend/internal/service/openai_codex_identity.go#L44-L53
"""

import re
import threading
import time
from typing import Callable, Dict

import httpx

from litellm._logging import verbose_logger
from litellm.llms.custom_httpx.http_handler import _get_httpx_client

CODEX_ORIGINATOR = "codex-tui"
# Floor for the advertised version until the first registry refresh lands; upstream 404s below 0.144.0.
CODEX_VERSION_FALLBACK = "0.154.0"
CODEX_PLATFORM = "(Ubuntu 24.4.0; x86_64) xterm-256color"
CODEX_LATEST_VERSION_URL = "https://registry.npmjs.org/@openai/codex/latest"
CODEX_VERSION_REFRESH_SECONDS = 6 * 60 * 60
CODEX_VERSION_FETCH_TIMEOUT_SECONDS = 10.0
CODEX_IDENTITY_HEADER_KEYS = ("originator", "user-agent", "version")
_CODEX_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


class CodexVersion:
    """Latest stable Codex release; refreshed in the background, never on the request path."""

    def __init__(
        self,
        fetch_latest: Callable[[], str],
        run_in_background: Callable[[Callable[[], None]], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch_latest = fetch_latest
        self._run_in_background = run_in_background
        self._clock = clock
        self._version = CODEX_VERSION_FALLBACK
        self._next_refresh_at = float("-inf")
        self._lock = threading.Lock()

    def get(self) -> str:
        now = self._clock()
        with self._lock:
            refresh_due = now >= self._next_refresh_at
            if refresh_due:
                self._next_refresh_at = now + CODEX_VERSION_REFRESH_SECONDS
        if refresh_due:
            self._run_in_background(self._refresh)
        return self._version

    def _refresh(self) -> None:
        try:
            latest = self._fetch_latest()
        except (httpx.HTTPError, ValueError) as e:
            verbose_logger.warning(
                "Codex version refresh failed, keeping %s", self._version, exc_info=e
            )
            return
        if _version_key(latest) > _version_key(self._version):
            self._version = latest


def codex_identity_headers() -> Dict[str, str]:
    version = _codex_version.get()
    return {
        "originator": CODEX_ORIGINATOR,
        "user-agent": f"{CODEX_ORIGINATOR}/{version} {CODEX_PLATFORM}",
        "version": version,
    }


def is_codex_identity_header(key: str) -> bool:
    return key.lower() in CODEX_IDENTITY_HEADER_KEYS


def apply_codex_identity_headers(headers: dict) -> None:
    """Replace caller-supplied identity headers (any case) in place with the Codex identity."""
    for key in list(headers):
        if is_codex_identity_header(str(key)):
            del headers[key]
    headers.update(codex_identity_headers())


def parse_codex_registry_version(payload: dict) -> str:
    version = payload.get("version")
    if not isinstance(version, str) or not _CODEX_VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"unexpected Codex version in npm registry: {version!r}")
    return version


def _fetch_latest_codex_version() -> str:
    response = _get_httpx_client().get(
        CODEX_LATEST_VERSION_URL, timeout=CODEX_VERSION_FETCH_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return parse_codex_registry_version(response.json())


def _run_in_daemon_thread(task: Callable[[], None]) -> None:
    threading.Thread(target=task, name="codex-version-refresh", daemon=True).start()


def _version_key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


_codex_version = CodexVersion(
    fetch_latest=_fetch_latest_codex_version,
    run_in_background=_run_in_daemon_thread,
)
