import asyncio

import fakeredis

from agentek_gateway.subscriptions.duties import (
    EGRESS_CYCLE_S,
    REFRESH_CYCLE_S,
    LeaderDuties,
)
from agentek_gateway.subscriptions.leader import LeaderLease

from .conftest import FakeClock
from .upkeep import build_upkeep

KEY = "t:leader"


def leases(count: int) -> tuple[list[LeaderLease], fakeredis.FakeServer]:
    server = fakeredis.FakeServer()
    return [
        LeaderLease(fakeredis.FakeAsyncRedis(server=server, decode_responses=True), KEY)
        for _ in range(count)
    ], server


async def test_only_one_replica_holds_the_lease_and_renews_it() -> None:
    (first, second), _ = leases(2)

    results = [
        await first.hold(),
        await second.hold(),
        await first.hold(),
        await second.hold(),
    ]

    assert results == [True, False, True, False]


async def test_lease_of_a_dead_leader_expires() -> None:
    (first, second), server = leases(2)
    await first.hold()
    await first.hold()
    redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

    await redis.pexpire(KEY, 1)
    await asyncio.sleep(0.01)

    assert await second.hold()


async def test_non_leader_runs_no_duties_and_the_leader_runs_them() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    (first, second), _ = leases(2)
    replica = upkeep.replica()
    clock = FakeClock()
    leader = LeaderDuties(first, replica.probes, replica.refresher, Counting(), clock)
    follower = LeaderDuties(
        second, replica.probes, replica.refresher, Counting(), clock
    )

    led = await leader.tick()
    followed = await follower.tick()

    assert (led, followed, len(upkeep.provider.refresh_tokens)) == (True, False, 1)


class Counting:
    def __init__(self) -> None:
        self.ticks = 0

    async def tick(self) -> None:
        self.ticks += 1


async def test_token_upkeep_runs_once_per_cycle_while_probes_run_every_tick() -> None:
    (lease,), _ = leases(1)
    probes, refresher, egress, clock = Counting(), Counting(), Counting(), FakeClock()
    duties = LeaderDuties(lease, probes, refresher, egress, clock)  # type: ignore[arg-type]

    await duties.tick()
    clock.advance(REFRESH_CYCLE_S / 2)
    await duties.tick()
    clock.advance(REFRESH_CYCLE_S / 2 + 1)
    await duties.tick()

    assert (probes.ticks, refresher.ticks, egress.ticks) == (3, 2, 1)


async def test_failure_inside_the_duties_does_not_stop_the_loop() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    (lease,), _ = leases(1)
    replica = upkeep.replica()

    class Broken:
        async def tick(self) -> None:
            raise RuntimeError("boom")

    duties = LeaderDuties(lease, Broken(), replica.refresher, Counting(), FakeClock())  # type: ignore[arg-type]

    assert await duties.tick() is True


async def test_egress_is_measured_every_ten_minutes() -> None:
    (lease,), _ = leases(1)
    probes, refresher, egress, clock = Counting(), Counting(), Counting(), FakeClock()
    duties = LeaderDuties(lease, probes, refresher, egress, clock)  # type: ignore[arg-type]

    await duties.tick()
    clock.advance(EGRESS_CYCLE_S - 1)
    await duties.tick()
    clock.advance(2)
    await duties.tick()

    assert egress.ticks == 2


class UnreachableLease:
    async def hold(self) -> bool:
        raise ConnectionError("redis down")


async def test_a_lease_that_cannot_be_checked_is_not_reported_as_leadership() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    probes, refresher, egress = Counting(), Counting(), Counting()
    duties = LeaderDuties(UnreachableLease(), probes, refresher, egress, FakeClock())  # type: ignore[arg-type]

    assert (await duties.tick(), probes.ticks, refresher.ticks, egress.ticks) == (
        False,
        0,
        0,
        0,
    )


async def test_the_catalog_runs_every_fifteen_seconds_and_only_on_the_leader() -> None:
    from agentek_gateway.subscriptions.model_copies import COPY_SYNC_INTERVAL_S

    (first, second), _ = leases(2)
    clock, catalog = FakeClock(), Counting()
    idle = Counting()
    leader = LeaderDuties(first, idle, idle, idle, clock, catalog)  # type: ignore[arg-type]
    follower = LeaderDuties(second, idle, idle, idle, clock, catalog)  # type: ignore[arg-type]

    await leader.tick()
    await follower.tick()
    await leader.tick()
    clock.advance(COPY_SYNC_INTERVAL_S - 1)
    await leader.tick()
    clock.advance(1)
    await leader.tick()

    assert catalog.ticks == 2
