from collections.abc import Mapping

import httpx

from .chatgpt import HttpReply

REQUEST_TIMEOUT_S = 30.0


class HttpxProbeTransport:
    """Provider probes and token refreshes over the gateway's own egress (proxy settings come from the environment)."""

    def __init__(self, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    async def post_json(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, object]
    ) -> HttpReply:
        async with httpx.AsyncClient(timeout=self._timeout_s, trust_env=True) as client:
            reply = await client.post(url, headers=dict(headers), json=payload)
        return HttpReply(reply.status_code, dict(reply.headers), reply.text)

    async def get(self, url: str, headers: Mapping[str, str]) -> HttpReply:
        async with httpx.AsyncClient(timeout=self._timeout_s, trust_env=True) as client:
            reply = await client.get(url, headers=dict(headers))
        return HttpReply(reply.status_code, dict(reply.headers), reply.text)
