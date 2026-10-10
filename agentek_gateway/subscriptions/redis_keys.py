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
    def enabled(self) -> str:
        return f"{self._prefix}enabled"

    @property
    def usage(self) -> str:
        return f"{self._prefix}usage"

    @property
    def changes(self) -> str:
        return f"{self._prefix}changes"

    @property
    def probed(self) -> str:
        return f"{self._prefix}probed"

    @property
    def egress(self) -> str:
        return f"{self._prefix}egress"

    @property
    def leader(self) -> str:
        return f"{self._prefix}leader"

    def refresh_lock(self, credential_name: str) -> str:
        return f"{self._prefix}refresh-lock:{credential_name}"

    def latest_auth(self, credential_name: str) -> str:
        return f"{self._prefix}latest-auth:{credential_name}"

    def refreshed(self, credential_name: str) -> str:
        return f"{self._prefix}refreshed:{credential_name}"

    def limits_refresh(self, subscription_id: SubscriptionId) -> str:
        return f"{self._prefix}limits-refresh:{subscription_id}"

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


def route_member(route: Route) -> str:
    return f"{route.provider}\x1f{route.egress or ''}"


def route_from_member(member: str) -> Route:
    provider, _, egress = member.partition("\x1f")
    return Route(provider, egress or None)
