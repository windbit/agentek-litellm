import logging

from agentek_gateway.subscriptions.egress import (
    TRACE_URL,
    EgressBook,
    EgressWatcher,
    parse_trace,
)
from agentek_gateway.subscriptions.model import EgressInfo, Route
from agentek_gateway.subscriptions.providers.chatgpt import HttpReply

from .conftest import FakeClock, make_subscription
from .plain import plain_runtime
from .stack import account_of, running_stack

TRACE = "fl=1\nh=chatgpt.com\nip=2001:db8::7\nts=1.5\nvisit_scheme=https\ncolo=ARN\nloc=FI\n"
NOW = 1_000_000.0


class FakeTransport:
    def __init__(self, reply: HttpReply | Exception) -> None:
        self.reply = reply
        self.requested: list[str] = []

    async def post_json(self, url, headers, payload):  # type: ignore[no-untyped-def]
        raise AssertionError("the watcher only reads")

    async def get(self, url, headers):  # type: ignore[no-untyped-def]
        self.requested.append(url)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_trace_gives_the_address_and_the_data_center() -> None:
    assert parse_trace(TRACE, NOW) == EgressInfo("2001:db8::7", "ARN", NOW)


def test_trace_without_a_data_center_is_not_used() -> None:
    assert parse_trace("ip=2001:db8::7\n", NOW) is None


def test_trace_that_is_not_key_value_text_is_not_used() -> None:
    assert parse_trace("<html>blocked</html>", NOW) is None


def test_book_describes_a_known_route_and_admits_an_unknown_one() -> None:
    book = EgressBook()
    book.replace({Route("chatgpt", None): EgressInfo("2001:db8::7", "ARN", NOW)})

    assert (book.note(Route("chatgpt", None)), book.note(Route("chatgpt", "eu"))) == (
        "egress_ip=2001:db8::7 colo=ARN",
        "egress=unknown",
    )


async def watcher_for(reply: HttpReply | Exception, *egress: str | None):  # type: ignore[no-untyped-def]
    plain = plain_runtime([])
    for index, name in enumerate(egress):
        plain.repo.put(make_subscription(f"s{index}", egress=name))
    transport = FakeTransport(reply)
    return EgressWatcher(transport, plain.store, plain.repo, FakeClock()), transport, plain  # type: ignore[arg-type]


async def test_each_distinct_route_is_measured_once() -> None:
    watcher, transport, plain = await watcher_for(
        HttpReply(200, {}, TRACE), "eu", "eu", None
    )

    await watcher.tick()

    assert (
        transport.requested,
        sorted(route.egress or "" for route in await plain.store.read_egress()),
    ) == (
        [TRACE_URL, TRACE_URL],
        ["", "eu"],
    )


async def test_failed_or_useless_trace_writes_nothing_and_does_not_raise() -> None:
    for reply in (
        ConnectionError("down"),
        HttpReply(503, {}, TRACE),
        HttpReply(200, {}, "nothing"),
    ):
        watcher, _, plain = await watcher_for(reply, "eu")

        await watcher.tick()

        assert await plain.store.read_egress() == {}


async def test_unclassified_error_is_logged_with_the_egress_address_and_data_center(caplog) -> None:  # type: ignore[no-untyped-def]
    async with running_stack(["a", "b"]) as stack:
        stack.runtime.parts.egress.replace(
            {Route("chatgpt", None): EgressInfo("2001:db8::7", "FRA", NOW)}
        )
        stack.mock.script(account_of("a"), "overloaded")

        with caplog.at_level(logging.WARNING):
            await stack.call()

        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "unclassified error" in message
            and "egress_ip=2001:db8::7 colo=FRA" in message
            for message in messages
        )
