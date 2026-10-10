"""Operator services over the in-memory catalog: scripted sign-in and usage check, audit and change notifications."""

from dataclasses import dataclass

from agentek_gateway.subscriptions.admin import AdminDeps, SubscriptionAdmin
from agentek_gateway.subscriptions.model import Limits, Subscription, Window
from agentek_gateway.subscriptions.providers.chatgpt import ChatgptAuth
from agentek_gateway.subscriptions.providers.chatgpt_login import DeviceLogin
from agentek_gateway.subscriptions.token_coordination import TokenCoordinator

from .catalog_stack import PROVIDER, CatalogStack, catalog_stack

OPERATOR = "operator-1"


class ScriptedLogin:
    def __init__(self) -> None:
        self.polls: list[ChatgptAuth | None] = []
        self.started = 0

    async def start(self) -> DeviceLogin:
        self.started += 1
        return DeviceLogin("device-1", "ABCD-1234", "https://login.example/device")

    async def poll(self, device_auth_id: str, user_code: str) -> ChatgptAuth | None:
        return self.polls.pop(0) if self.polls else None


class ScriptedUsage:
    def __init__(self) -> None:
        self.calls = 0
        self.limits: Limits | None = Limits(weekly=Window(40.0, 4_000_000_000.0))

    async def probe_usage(self, auth: ChatgptAuth, *, now: float) -> Limits | None:
        self.calls += 1
        return self.limits


@dataclass
class AdminStack:
    base: CatalogStack
    login: ScriptedLogin
    usage: ScriptedUsage
    admin: SubscriptionAdmin
    coordinator: TokenCoordinator
    changes: list[int]


def admin_stack(subscriptions: list[Subscription] | None = None) -> AdminStack:
    base = catalog_stack(subscriptions)
    login, usage = ScriptedLogin(), ScriptedUsage()
    coordinator = TokenCoordinator(base.redis, base.keys)
    changes: list[int] = []
    admin = SubscriptionAdmin(
        AdminDeps(
            clock=base.clock,
            repo=base.repo,
            writer=base.writer,
            directory=base.directory,
            credentials=base.tokens,
            store=base.store,
            toggle=base.toggle,
            states=base.states,
            coordinator=coordinator,
            copies=base.copies,
            settings=base.settings,
            audit=base.audit,
            usage_providers={PROVIDER: usage},
            logins={PROVIDER: login},
            on_changed=lambda: changes.append(1),
        )
    )
    return AdminStack(base, login, usage, admin, coordinator, changes)
