"""Metrics read /proc, cgroups and Docker's config files, and report rates between samples."""

import asyncio
import json
from pathlib import Path

import pytest

from agent.metrics import Metrics
from agent.validators import InvalidArgs

PROJECT = "wolfbet-dashboard"
USER_SLICE = "user.slice/user-0.slice/user@0.service/app.slice"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Host:
    """A fake /proc and /sys/fs/cgroup tree, rewritten between samples."""

    def __init__(self, root: Path) -> None:
        self.proc = root / "proc"
        self.cgroup = root / "cgroup"
        self.docker = root / "docker"
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "net").mkdir()
        (self.proc / "self" / "cgroup").write_text(f"0::/{USER_SLICE}/casino-agent.service\n")
        (self.proc / "loadavg").write_text("0.50 0.40 0.30 1/200 999\n")
        (self.proc / "meminfo").write_text("MemTotal: 2000 kB\nMemAvailable: 500 kB\n")
        self.cpu(busy=0, idle=0)
        self.net(rx=0, tx=0)

    def cpu(self, busy: int, idle: int) -> None:
        (self.proc / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 2 3 4\n")

    def net(self, rx: int, tx: int) -> None:
        (self.proc / "net" / "dev").write_text(
            "Inter-|   Receive\n face |bytes\n"
            "    lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n"
            f"  eth0: {rx} 0 0 0 0 0 0 0 {tx} 0 0 0 0 0 0 0\n"
        )

    def unit(self, relative: str, usage_usec: int, anon: int) -> None:
        directory = self.cgroup / USER_SLICE / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 1\n")
        (directory / "memory.stat").write_text(f"anon {anon}\nfile 7\n")

    def container(
        self, cid: str, service: str, usage_usec: int, memory: int, project: str = PROJECT
    ) -> None:
        directory = self.cgroup / "system.slice" / f"docker-{cid}.scope"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cpu.stat").write_text(f"usage_usec {usage_usec}\n")
        (directory / "memory.current").write_text(f"{memory}\n")
        config = self.docker / cid / "config.v2.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        labels = {"com.docker.compose.project": project, "com.docker.compose.service": service}
        config.write_text(json.dumps({"Config": {"Labels": labels}}))


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def metrics(host: Host, clock: Clock) -> Metrics:
    return Metrics(
        PROJECT,
        proc=host.proc,
        cgroup=host.cgroup,
        docker=host.docker,
        clock=clock,
        disk=lambda: (1000, 250),
        uid=0,
    )


def test_host_load_memory_cpu_and_network(host: Host, clock: Clock, metrics: Metrics) -> None:
    first = metrics.sample()["host"]
    assert (first["cpu_pct"], first["net_rx_bps"]) == (None, None)
    assert first["load"] == [0.5, 0.4, 0.3]
    assert (first["mem_total"], first["mem_available"]) == (2000 * 1024, 500 * 1024)
    assert (first["disk_total"], first["disk_free"]) == (1000, 250)
    host.cpu(busy=30, idle=70)
    host.net(rx=4000, tx=1000)
    clock.now += 2
    second = metrics.sample()["host"]
    assert second["cpu_pct"] == 30.0
    assert (second["net_rx_bps"], second["net_tx_bps"]) == (2000.0, 500.0)


def test_bot_units_are_found_next_to_the_agent(host: Host, clock: Clock, metrics: Metrics) -> None:
    host.unit("wolfbet.service", usage_usec=0, anon=10_000)
    host.unit("app-wolfbet\\x2dlane.slice/wolfbet-lane@2.service", usage_usec=0, anon=20_000)
    host.unit("other.service", usage_usec=0, anon=1)
    metrics.sample()
    host.unit("wolfbet.service", usage_usec=500_000, anon=10_000)
    host.unit(
        "app-wolfbet\\x2dlane.slice/wolfbet-lane@2.service", usage_usec=2_000_000, anon=20_000
    )
    clock.now += 2
    units = metrics.sample()["units"]
    assert units == [
        {"unit": "wolfbet", "cpu_pct": 25.0, "rss_bytes": 10_000},
        {"unit": "wolfbet-lane@2", "cpu_pct": 100.0, "rss_bytes": 20_000},
    ]


def test_a_system_unit_agent_still_finds_the_user_managers_bot_units(
    host: Host, clock: Clock, metrics: Metrics
) -> None:
    (host.proc / "self" / "cgroup").write_text("0::/system.slice/casino-agent.service\n")
    (host.cgroup / "system.slice").mkdir(parents=True, exist_ok=True)
    host.unit("wolfbet.service", usage_usec=0, anon=10_000)
    host.unit("app-wolfbet\\x2dlane.slice/wolfbet-lane@3.service", usage_usec=0, anon=30_000)
    names = [unit["unit"] for unit in metrics.sample()["units"]]
    assert names == ["wolfbet", "wolfbet-lane@3"]


def test_only_this_projects_redis_and_timescale_are_reported(
    host: Host, clock: Clock, metrics: Metrics
) -> None:
    host.container("aaa", "redis", usage_usec=0, memory=111)
    host.container("bbb", "timescale", usage_usec=0, memory=222)
    host.container("ccc", "redis", usage_usec=0, memory=333, project="someone-else")
    host.container("ddd", "api", usage_usec=0, memory=444)
    broken = host.cgroup / "system.slice" / "docker-eee.scope"
    broken.mkdir()
    (host.docker / "eee").mkdir()
    (host.docker / "eee" / "config.v2.json").write_text("{not json")
    metrics.sample()
    host.container("aaa", "redis", usage_usec=1_000_000, memory=111)
    clock.now += 1
    containers = metrics.sample()["containers"]
    assert containers == [
        {"service": "redis", "cpu_pct": 100.0, "memory_bytes": 111},
        {"service": "timescale", "cpu_pct": 0.0, "memory_bytes": 222},
    ]


def test_a_second_call_within_a_second_is_served_from_cache(
    host: Host, clock: Clock, metrics: Metrics
) -> None:
    first = metrics.sample()
    host.cpu(busy=50, idle=50)
    clock.now += 0.5
    assert metrics.sample() is first
    clock.now += 0.6
    assert metrics.sample() is not first


def test_the_op_takes_no_arguments(metrics: Metrics) -> None:
    with pytest.raises(InvalidArgs):
        asyncio.run(metrics.handler({"path": "/etc/shadow"}))
    assert "host" in asyncio.run(metrics.handler({}))
