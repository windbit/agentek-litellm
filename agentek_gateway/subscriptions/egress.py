from collections.abc import Mapping

from litellm._logging import verbose_proxy_logger

from .clock import Clock
from .model import EgressInfo, Route
from .ports import StateStore, SubscriptionRepo
from .providers.chatgpt import ProbeTransport

TRACE_URL = "https://chatgpt.com/cdn-cgi/trace"
OK_STATUS = 200


def parse_trace(body: str, now: float) -> EgressInfo | None:
    fields = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
    ip, colo = fields.get("ip"), fields.get("colo")
    if not ip or not colo:
        return None
    return EgressInfo(ip=ip, colo=colo, observed_at=now)


class EgressBook:
    """Where each route leaves the gateway, as last measured; used to annotate error logs without I/O."""

    def __init__(self) -> None:
        self._items: Mapping[Route, EgressInfo] = {}

    def replace(self, items: Mapping[Route, EgressInfo]) -> None:
        self._items = dict(items)

    def note(self, route: Route) -> str:
        info = self._items.get(route)
        return f"egress_ip={info.ip} colo={info.colo}" if info else "egress=unknown"


class EgressWatcher:
    """Leader task: measures the egress address and the provider data center a few times an hour."""

    def __init__(
        self,
        transport: ProbeTransport,
        store: StateStore,
        repo: SubscriptionRepo,
        clock: Clock,
    ) -> None:
        self._transport = transport
        self._store = store
        self._repo = repo
        self._clock = clock

    async def tick(self) -> None:
        routes = {
            Route(subscription.provider, subscription.egress)
            for subscription in await self._repo.list_subscriptions()
        }
        for route in sorted(
            routes, key=lambda item: (item.provider, item.egress or "")
        ):
            await self._measure(route)

    async def _measure(self, route: Route) -> None:
        try:
            reply = await self._transport.get(TRACE_URL, {})
        except Exception:  # noqa: BLE001
            verbose_proxy_logger.exception("agentek_gateway egress trace failed")
            return
        info = (
            parse_trace(reply.body, self._clock.now())
            if reply.status == OK_STATUS
            else None
        )
        if info is None:
            verbose_proxy_logger.warning(
                "agentek_gateway egress trace answered %s without ip and colo",
                reply.status,
            )
            return
        await self._store.write_egress(route, info)
