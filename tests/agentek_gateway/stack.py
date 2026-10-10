"""A real Router wired to the subscription plugin and a mock Codex backend, driven the way the proxy drives it."""

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import fakeredis
import litellm
from litellm import Router
from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

from agentek_gateway.subscriptions.adapter import SubscriptionCallback
from agentek_gateway.subscriptions.config import GatewayConfig
from agentek_gateway.subscriptions.notify import RedisListener, RedisNotifier
from agentek_gateway.subscriptions.redis_keys import Keys
from agentek_gateway.subscriptions.redis_state import RedisStateStore
from agentek_gateway.subscriptions.snapshot import SnapshotTiming
from agentek_gateway.subscriptions.state_db import InMemoryStateDb
from agentek_gateway.subscriptions.memory import (
    InMemoryPolicyRepo,
    InMemorySubscriptionRepo,
)
from agentek_gateway.subscriptions.model import Subscription
from agentek_gateway.subscriptions.ports import SlotStore, StateStore
from agentek_gateway.subscriptions.providers.chatgpt import ChatGPTProvider
from agentek_gateway.subscriptions.providers.observer import (
    install_error_observer,
    uninstall_error_observer,
)
from agentek_gateway.subscriptions.redis_slots import RedisSlotStore
from agentek_gateway.subscriptions.runtime import (
    RuntimeSlot,
    SubscriptionRuntime,
    RuntimeDeps,
    build_runtime,
)

from .conftest import FakeClock
from .mock_codex import MockCodex

MODEL = "gpt-5.4"
FAR_FUTURE = 4_102_444_800
CHAT_MESSAGES = [{"role": "user", "content": "x"}]


CALLBACK_LISTS = (
    "callbacks",
    "success_callback",
    "failure_callback",
    "_async_success_callback",
    "_async_failure_callback",
)


def saved_callbacks() -> dict[str, list[object]]:
    return {name: list(getattr(litellm, name)) for name in CALLBACK_LISTS}


class Switches:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.failures: list[tuple[str, str]] = []

    def switched(self, subscription: Subscription, reason: object) -> None:
        self.events.append((subscription.id, str(reason)))

    def failed(self, subscription: Subscription, reason: object) -> None:
        self.failures.append((subscription.id, str(reason)))


def account_of(sub_id: str) -> str:
    return f"acct-{sub_id}"


def deployment_for(
    sub_id: str, base_url: str, model: str = MODEL, mode: str = "responses"
) -> dict[str, object]:
    return {
        "model_name": model,
        "litellm_params": {
            "model": f"chatgpt/{model}",
            "chatgpt_api_base": base_url,
            "litellm_credential_name": f"cred-{sub_id}",
            "chatgpt_auth": {
                "access_token": "fake.jwt.token",
                "refresh_token": "rt-fake",
                "expires_at": FAR_FUTURE,
                "account_id": account_of(sub_id),
            },
        },
        "model_info": {"id": f"sub:{sub_id}:{model}", "mode": mode},
    }


@dataclass
class Shared:
    """What replicas of one gateway have in common: Redis, the database and the subscription records."""

    server: fakeredis.FakeServer = field(default_factory=fakeredis.FakeServer)
    db: InMemoryStateDb = field(default_factory=InMemoryStateDb)
    repo: InMemorySubscriptionRepo = field(default_factory=InMemorySubscriptionRepo)


@dataclass
class Stack:
    mock: MockCodex
    router: Router
    callback: SubscriptionCallback
    runtime: SubscriptionRuntime
    clock: FakeClock
    store: StateStore
    slot_store: SlotStore
    switches: Switches
    subscriptions: dict[str, Subscription]
    redis: fakeredis.FakeAsyncRedis
    callbacks_before: dict[str, list[object]] = field(default_factory=dict)
    last_data: dict[str, object] = field(default_factory=dict)

    async def call(self, request_id: str | None = None, **overrides: object) -> object:
        """One chat request the way the proxy sends it: pre-call hook, router, final-failure hook."""
        data: dict[str, object] = {
            "model": MODEL,
            "messages": CHAT_MESSAGES,
            "metadata": {},
            **overrides,
        }
        self.last_data = data
        await self.callback.async_pre_call_hook(None, None, data, "acompletion")  # type: ignore[arg-type]
        if request_id:
            data["metadata"]["agentek_request_id"] = request_id  # type: ignore[index]
        try:
            return await self.router.acompletion(**data)
        except Exception as error:
            await self.callback.async_post_call_failure_hook(data, error, None)  # type: ignore[arg-type]
            raise
        finally:
            await self.finished()

    async def respond(self, **overrides: object) -> object:
        """A non-streaming Responses request through the same proxy-like path."""
        data: dict[str, object] = {
            "model": MODEL,
            "input": "hi",
            "litellm_metadata": {},
            **overrides,
        }
        self.last_data = data
        await self.callback.async_pre_call_hook(None, None, data, "aresponses")  # type: ignore[arg-type]
        try:
            return await self.router.aresponses(**data)
        except Exception as error:
            await self.callback.async_post_call_failure_hook(data, error, None)  # type: ignore[arg-type]
            raise
        finally:
            await self.finished()

    async def open_stream(self, **overrides: object) -> AsyncGenerator[object, None]:
        """A Responses stream as the proxy serves it: header hook at the start, chunks through the iterator hook."""
        data: dict[str, object] = {
            "model": MODEL,
            "input": "hi",
            "stream": True,
            "litellm_metadata": {},
            **overrides,
        }
        self.last_data = data
        await self.callback.async_pre_call_hook(None, None, data, "aresponses")  # type: ignore[arg-type]
        try:
            response = await self.router.aresponses(**data)
        except Exception as error:
            await self.callback.async_post_call_failure_hook(data, error, None)  # type: ignore[arg-type]
            raise
        await self.callback.async_post_call_response_headers_hook(data, None, response)  # type: ignore[arg-type]
        return self.callback.async_post_call_streaming_iterator_hook(None, response, data)  # type: ignore[arg-type,return-value]

    async def response_headers(self) -> dict[str, str] | None:
        """What the proxy's failure-path header hook adds to the last request's error response."""
        return await self.callback.async_post_call_response_headers_hook(
            self.last_data, None, None  # type: ignore[arg-type]
        )

    async def settle(self) -> None:
        await asyncio.sleep(0.05)
        await self.runtime.parts.tasks.drain()

    async def finished(self, timeout_s: float = 3.0) -> None:
        """Waits until the plugin has closed every request it tracked (log callbacks run in the background)."""
        parts = self.runtime.parts
        deadline = time.monotonic() + timeout_s
        while (
            parts.ledger.size() or parts.attempts.size()
        ) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await self.settle()

    async def refresh(self) -> None:
        await self.runtime.parts.snapshot.refresh()

    async def close(self) -> None:
        uninstall_error_observer(ChatGPTResponsesAPIConfig)
        for name, items in self.callbacks_before.items():
            setattr(litellm, name, items)
        await self.runtime.parts.tasks.cancel_all()
        await self.runtime.writes.cancel_all()
        await self.mock.stop()
        await self.redis.aclose()


async def _build_stack(
    sub_ids: list[str],
    *,
    shared: Shared | None = None,
    live: bool = False,
    listen: bool = True,
    snapshot_interval_s: float = 1.0,
    slot_limit: int | None = None,
    config: GatewayConfig | None = None,
    num_retries: int = 4,
    priorities: dict[str, int] | None = None,
    store_class: type[RedisStateStore] = RedisStateStore,
) -> Stack:
    callbacks_before = saved_callbacks()
    mock = MockCodex()
    await mock.start()
    clock = FakeClock(start=time.time())
    config = config or GatewayConfig()
    shared = shared or Shared()
    redis = fakeredis.FakeAsyncRedis(server=shared.server, decode_responses=True)
    keys = Keys("t:")
    subscriptions = {
        sub_id: Subscription(
            id=sub_id,
            provider="chatgpt",
            name=f"name-{sub_id}",
            credential_name=f"cred-{sub_id}",
            concurrency_limit=slot_limit,
            priority=(priorities or {}).get(sub_id, 50),
        )
        for sub_id in sub_ids
    }
    for subscription in subscriptions.values():
        if subscription.id not in await _known(shared.repo):
            shared.repo.put(subscription)
    state_store = store_class(
        redis, shared.db, clock, keys, RedisNotifier(redis, keys.changes)
    )
    slot_store = RedisSlotStore(redis, clock, keys.prefix)
    router = Router(
        model_list=[deployment_for(sub_id, mock.base_url) for sub_id in sub_ids],
        num_retries=num_retries,
        retry_after=0,
    )
    provider = ChatGPTProvider(transport=None, probe_model=MODEL)  # type: ignore[arg-type]
    switches = Switches()
    runtime = build_runtime(
        RuntimeDeps(
            clock=clock,
            config=config,
            state_store=state_store,
            slot_store=slot_store,
            repo=shared.repo,
            policy=InMemoryPolicyRepo(),
            providers={"chatgpt": provider},
            model_list=lambda: router.model_list,
            telemetry=switches,
            timing=SnapshotTiming(interval_s=snapshot_interval_s),
        )
    )
    snapshot = runtime.parts.snapshot
    slot = RuntimeSlot(runtime)
    callback = SubscriptionCallback(slot)
    litellm.callbacks.append(callback)
    install_error_observer(
        ChatGPTResponsesAPIConfig,
        lambda status, headers, body: provider.classify_error(
            status, headers, body, now=clock.now()
        ),
        runtime.outcomes.on_observed,
    )
    await snapshot.refresh()
    if live:
        runtime.parts.tasks.spawn(snapshot.run())
        if listen:
            runtime.parts.tasks.spawn(
                RedisListener(redis, keys.changes, snapshot.request_refresh).run()
            )
    return Stack(
        mock=mock,
        router=router,
        callback=callback,
        runtime=runtime,
        clock=clock,
        store=state_store,
        slot_store=slot_store,
        switches=switches,
        subscriptions=subscriptions,
        redis=redis,
        callbacks_before=callbacks_before,
    )


@asynccontextmanager
async def running_stack(sub_ids: list[str], **options: object) -> AsyncIterator[Stack]:
    stack = await _build_stack(sub_ids, **options)  # type: ignore[arg-type]
    try:
        yield stack
    finally:
        await stack.close()


async def _known(repo: InMemorySubscriptionRepo) -> set[str]:
    return {subscription.id for subscription in await repo.list_subscriptions()}
