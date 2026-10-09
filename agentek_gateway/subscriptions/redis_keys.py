from .model import Route, SubscriptionId


class Keys:
    """Redis key layout of the plugin; every key lives under one prefix so environments can share a Redis."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def state_pattern(self) -> str:
        return f"{self._prefix}state:*"

    @property
    def degraded(self) -> str:
        return f"{self._prefix}degraded"

    @property
    def unsupported(self) -> str:
        return f"{self._prefix}unsupported"

    @property
    def usage(self) -> str:
        return f"{self._prefix}usage"

    @property
    def changes(self) -> str:
        return f"{self._prefix}changes"

    def state(self, subscription_id: SubscriptionId) -> str:
        return f"{self._prefix}state:{subscription_id}"

    def state_id(self, key: str) -> SubscriptionId:
        return key.removeprefix(f"{self._prefix}state:")

    def errors(self, subscription_id: SubscriptionId) -> str:
        return f"{self._prefix}errors:{subscription_id}"

    def series(self, subscription_id: SubscriptionId) -> str:
        return f"{self._prefix}series:{subscription_id}"

    def sticky(self, digest: str) -> str:
        return f"{self._prefix}sticky:{digest}"

    def slots(self, subscription_id: SubscriptionId) -> str:
        return f"{self._prefix}slots:{subscription_id}"


def route_member(route: Route) -> str:
    return f"{route.provider}\x1f{route.egress or ''}"


def route_from_member(member: str) -> Route:
    provider, _, egress = member.partition("\x1f")
    return Route(provider, egress or None)
