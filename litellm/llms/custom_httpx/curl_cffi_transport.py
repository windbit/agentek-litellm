from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, List, Optional, Tuple

import httpx

from litellm._logging import verbose_logger

if TYPE_CHECKING:
    from curl_cffi.requests import AsyncSession
    from curl_cffi.requests import Response as CurlResponse

# httpx auto-populates these on every request; left intact they leak a non-browser
# fingerprint at the HTTP layer and undo the TLS impersonation. Dropping them lets
# curl_cffi fill in browser-consistent values that match its JA3/JA4 handshake.
_FINGERPRINT_HEADERS = ("user-agent", "accept-encoding", "connection")


def _browser_headers(request: httpx.Request) -> List[Tuple[str, str]]:
    return [(name, value) for name, value in request.headers.multi_items() if name.lower() not in _FINGERPRINT_HEADERS]


class _CurlCffiByteStream(httpx.AsyncByteStream):
    def __init__(self, response: "CurlResponse") -> None:
        self._response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._response.aiter_content():
            yield chunk

    async def aclose(self) -> None:
        await self._response.aclose()


class CurlCffiAsyncTransport(httpx.AsyncBaseTransport):
    """httpx transport that routes requests through curl_cffi with browser TLS
    impersonation, so Cloudflare-fronted hosts (chatgpt.com) accept the JA3/JA4
    handshake instead of serving a managed challenge to stock httpx/aiohttp."""

    def __init__(self, impersonate: str) -> None:
        from curl_cffi.requests import AsyncSession

        self._session: "AsyncSession" = AsyncSession()
        self._impersonate = impersonate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        response = await self._session.request(
            method=request.method,
            url=str(request.url),
            headers=_browser_headers(request),
            data=request.content,
            stream=True,
            impersonate=self._impersonate,
            allow_redirects=True,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=list(response.headers.items()),
            stream=_CurlCffiByteStream(response),
            request=request,
        )

    async def aclose(self) -> None:
        # curl_cffi raises during multi-handle cleanup when the session is torn down
        # outside the loop that drove its requests; a best-effort close keeps a cached
        # client's shutdown from surfacing that as a request-path error.
        try:
            await self._session.close()
        except Exception as exc:
            verbose_logger.debug(f"curl_cffi session close failed: {exc}")


def build_curl_cffi_transport(impersonate: str) -> Optional[CurlCffiAsyncTransport]:
    try:
        return CurlCffiAsyncTransport(impersonate)
    except ImportError:
        verbose_logger.warning(
            "curl_cffi not installed; ChatGPT TLS impersonation disabled, falling back to the default transport"
        )
        return None
