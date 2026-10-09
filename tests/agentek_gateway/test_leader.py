import asyncio

import fakeredis

from agentek_gateway.subscriptions.duties import REFRESH_CYCLE_S, LeaderDuties
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


async def test_lease_passes_on_after_the_leader_releases_it() -> None:
    (first, second), _ = leases(2)
    await first.hold()

    await first.release()

    assert (await second.hold(), await first.hold()) == (True, False)


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
    leader = LeaderDuties(first, replica.probes, replica.refresher, clock)
    follower = LeaderDuties(second, replica.probes, replica.refresher, clock)

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
    probes, refresher, clock = Counting(), Counting(), FakeClock()
    duties = LeaderDuties(lease, probes, refresher, clock)  # type: ignore[arg-type]

    await duties.tick()
    clock.advance(REFRESH_CYCLE_S / 2)
    await duties.tick()
    clock.advance(REFRESH_CYCLE_S / 2 + 1)
    await duties.tick()

    assert (probes.ticks, refresher.ticks) == (3, 2)


async def test_failure_inside_the_duties_does_not_stop_the_loop() -> None:
    upkeep, _ = build_upkeep(["a"], expires_in_s=60)
    (lease,), _ = leases(1)
    replica = upkeep.replica()

    class Broken:
        async def tick(self) -> None:
            raise RuntimeError("boom")

    duties = LeaderDuties(lease, Broken(), replica.refresher, FakeClock())  # type: ignore[arg-type]

    assert await duties.tick() is True
