"""Host, unit and container load for the dashboard's system panel, read from /proc and cgroups.

Everything comes from files: no command runs and the Docker socket is never touched. Rates
(CPU %, network bytes/s) are deltas against the previous sample, and a sample is reused for
`CACHE_S`, so any number of callers costs one read per second.

The bot's units are found next to the agent's own unit in the cgroup tree, so the same code
works wherever systemd places the user manager. Containers are named from Docker's own
`config.v2.json` labels, and only the dashboard project's Redis and Timescale are reported.
"""

import json
import os
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.validators import InvalidArgs

DEFAULT_PROJECT = "wolfbet-dashboard"
CACHE_S = 1.0
NAMES_EVERY_S = 60.0
CONTAINER_SERVICES = frozenset({"redis", "timescale"})
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"


@dataclass
class _Raw:
    at: float
    cpu_busy: int
    cpu_total: int
    net_rx: int
    net_tx: int
    usage_usec: dict[str, int] = field(default_factory=dict)


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _keyed(text: str | None) -> dict[str, int]:
    """`key value` lines (cpu.stat, memory.stat) as integers."""
    out: dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _rate(now: int, before: int | None, seconds: float) -> float | None:
    if before is None or seconds <= 0 or now < before:
        return None
    return round((now - before) / seconds, 1)


class Metrics:
    """One sampler per agent; the roots are parameters so tests can point them at fixtures."""

    def __init__(
        self,
        project: str,
        units: tuple[str, ...] = ("wolfbet",),
        proc: Path = Path("/proc"),
        cgroup: Path = Path("/sys/fs/cgroup"),
        docker: Path = Path("/var/lib/docker/containers"),
        clock: Callable[[], float] = time.monotonic,
        disk: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.project = project
        self.units = units
        self.proc = proc
        self.cgroup = cgroup
        self.docker = docker
        self.clock = clock
        self.disk = disk or _root_disk
        self._previous: _Raw | None = None
        self._cached: tuple[float, dict[str, Any]] | None = None
        self._names: dict[str, str | None] = {}
        self._names_at = 0.0

    async def handler(self, args: dict[str, Any]) -> dict[str, Any]:
        """The `metrics` op; it takes no arguments."""
        if args:
            raise InvalidArgs("metrics takes no arguments")
        return self.sample()

    def sample(self) -> dict[str, Any]:
        now = self.clock()
        if self._cached is not None and now - self._cached[0] < CACHE_S:
            return self._cached[1]
        busy, total = self._cpu()
        rx, tx = self._net()
        units = dict(self._unit_dirs())
        containers = self._container_dirs(now)
        raw = _Raw(now, busy, total, rx, tx)
        for name, directory in [*units.items(), *containers.items()]:
            usage = _keyed(_read(directory / "cpu.stat")).get("usage_usec")
            if usage is not None:
                raw.usage_usec[name] = usage
        before = self._previous
        seconds = now - before.at if before else 0.0
        disk_total, disk_free = self.disk()
        result = {
            "host": {
                "cpu_pct": self._cpu_pct(raw, before),
                "load": self._load(),
                **self._memory(),
                "disk_total": disk_total,
                "disk_free": disk_free,
                "net_rx_bps": _rate(rx, before.net_rx if before else None, seconds),
                "net_tx_bps": _rate(tx, before.net_tx if before else None, seconds),
            },
            "units": [
                {
                    "unit": name,
                    "cpu_pct": self._usage_pct(name, raw, before, seconds),
                    "rss_bytes": _keyed(_read(directory / "memory.stat")).get("anon"),
                }
                for name, directory in sorted(units.items())
            ],
            "containers": [
                {
                    "service": name,
                    "cpu_pct": self._usage_pct(name, raw, before, seconds),
                    "memory_bytes": _int(_read(directory / "memory.current")),
                }
                for name, directory in sorted(containers.items())
            ],
        }
        self._previous = raw
        self._cached = (now, result)
        return result

    def _cpu(self) -> tuple[int, int]:
        line = (_read(self.proc / "stat") or "").splitlines()[:1]
        fields = [int(v) for v in line[0].split()[1:]] if line else []
        total = sum(fields)
        idle = sum(fields[3:5])
        return total - idle, total

    @staticmethod
    def _cpu_pct(raw: _Raw, before: _Raw | None) -> float | None:
        if before is None or raw.cpu_total <= before.cpu_total:
            return None
        busy = raw.cpu_busy - before.cpu_busy
        return round(100 * busy / (raw.cpu_total - before.cpu_total), 1)

    @staticmethod
    def _usage_pct(name: str, raw: _Raw, before: _Raw | None, seconds: float) -> float | None:
        previous = before.usage_usec.get(name) if before else None
        rate = _rate(raw.usage_usec.get(name, -1), previous, seconds)
        return None if rate is None else round(rate / 10_000, 1)

    def _load(self) -> list[float] | None:
        parts = (_read(self.proc / "loadavg") or "").split()
        try:
            return [float(v) for v in parts[:3]] if len(parts) >= 3 else None
        except ValueError:
            return None

    def _memory(self) -> dict[str, int | None]:
        info = {}
        for line in (_read(self.proc / "meminfo") or "").splitlines():
            key, _, rest = line.partition(":")
            value = rest.split()
            if value and value[0].isdigit():
                info[key] = int(value[0]) * 1024
        return {"mem_total": info.get("MemTotal"), "mem_available": info.get("MemAvailable")}

    def _net(self) -> tuple[int, int]:
        rx = tx = 0
        for line in (_read(self.proc / "net" / "dev") or "").splitlines()[2:]:
            name, _, counters = line.partition(":")
            values = counters.split()
            if name.strip() == "lo" or len(values) < 9:
                continue
            rx += int(values[0])
            tx += int(values[8])
        return rx, tx

    def _own_cgroup_parent(self) -> Path | None:
        for line in (_read(self.proc / "self" / "cgroup") or "").splitlines():
            if line.startswith("0::"):
                return self.cgroup / line[3:].strip().lstrip("/") / ".."
        return None

    def _unit_dirs(self) -> Iterator[tuple[str, Path]]:
        parent = self._own_cgroup_parent()
        if parent is None:
            return
        parent = parent.resolve()
        for prefix in self.units:
            for pattern in (f"{prefix}.service", f"{prefix}-lane@*.service"):
                for directory in [*parent.glob(pattern), *parent.glob(f"*.slice/{pattern}")]:
                    if directory.is_dir():
                        yield directory.name.removesuffix(".service"), directory

    def _container_dirs(self, now: float) -> dict[str, Path]:
        candidates: dict[str, Path] = {}
        for directory in (self.cgroup / "system.slice").glob("docker-*.scope"):
            candidates[directory.name.removeprefix("docker-").removesuffix(".scope")] = directory
        for directory in (self.cgroup / "docker").glob("*"):
            if directory.is_dir():
                candidates[directory.name] = directory
        names = self._container_names(now, candidates.keys())
        return {names[cid]: path for cid, path in candidates.items() if cid in names}

    def _container_names(self, now: float, ids: Iterable[str]) -> dict[str, str]:
        """Each container id's compose service, read once per id and again every minute."""
        if now - self._names_at >= NAMES_EVERY_S:
            self._names, self._names_at = {}, now
        for cid in ids:
            if cid not in self._names:
                self._names[cid] = self._service_of(cid)
        return {cid: name for cid, name in self._names.items() if name is not None}

    def _service_of(self, cid: str) -> str | None:
        try:
            config = json.loads(_read(self.docker / cid / "config.v2.json") or "")
            labels = config["Config"]["Labels"] or {}
        except (ValueError, KeyError, TypeError):
            return None
        service = labels.get(SERVICE_LABEL)
        if labels.get(PROJECT_LABEL) == self.project and service in CONTAINER_SERVICES:
            return str(service)
        return None


def _int(text: str | None) -> int | None:
    value = (text or "").strip()
    return int(value) if value.isdigit() else None


def _root_disk() -> tuple[int, int]:
    stats = os.statvfs("/")
    return stats.f_blocks * stats.f_frsize, stats.f_bavail * stats.f_frsize
