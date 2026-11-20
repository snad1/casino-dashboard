"""Long-running commands as jobs: start, stream, cancel, get, and an audit trail.

A job runs one long-running service command in its own process group, with the
command's timeout. Its output is kept as numbered lines in a bounded ring buffer
(each line at most 8 KiB, ANSI and control characters removed) so a client can
follow it live and resume from a sequence number after a reconnect. A service has
at most one mutating job at a time. Each start and finish becomes an audit record
the api pulls with a cursor. Jobs live in memory only; an agent restart forgets them.
"""

import asyncio
import contextlib
import logging
import os
import re
import signal
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from agent.errors import Busy, NotFound
from agent.files import FileStore
from agent.peercred import current_peer
from agent.protocol import Streaming
from agent.runner import spawn
from agent.validators import Args, InvalidArgs, parse_args

log = logging.getLogger("casino-agent")

LINE_CAP = 8 * 1024
BUFFER_LINES = 5000
AUDIT_RECORDS = 10_000
KEEP_FINISHED_S = 3600
MAX_JOBS = 200
CANCEL_GRACE_S = 5.0
_TRUNCATED = " [truncated]"
_CONTROL = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI sequences (colours, cursor moves)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences (titles, links)
    r"|\x1b[@-_]"  # other two-byte escapes
    r"|[\x00-\x08\x0a-\x1f\x7f]"  # control characters except tab
)
Status = Literal["running", "succeeded", "failed", "cancelled", "timeout"]
Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class PreparedCommand(Protocol):
    @property
    def args(self) -> dict[str, Any]: ...
    @property
    def argv(self) -> list[str]: ...
    @property
    def timeout_s(self) -> float: ...
    @property
    def cwd(self) -> Path | None: ...
    @property
    def mutating(self) -> bool: ...
    @property
    def file_id(self) -> str | None: ...
    @property
    def redact(self) -> tuple[str, ...]: ...


class JobService(Protocol):
    """What the job manager needs from a bot's service module."""

    def long_running(self, name: str) -> bool: ...

    def prepare(self, name: str, raw_args: dict[str, Any]) -> PreparedCommand: ...

    def after_job(self, name: str) -> None: ...


def clean_line(raw: bytes) -> str:
    """One output line as display-safe text: at most LINE_CAP bytes, no escapes or controls."""
    truncated = len(raw) > LINE_CAP
    text = _CONTROL.sub("", raw[:LINE_CAP].decode("utf-8", "replace"))
    return text + _TRUNCATED if truncated else text


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Job:
    id: str
    service: str
    cmd: str
    args: dict[str, Any]
    peer_uid: int | None
    mutating: bool
    started_ms: int
    buffer_lines: int = BUFFER_LINES
    status: Status = "running"
    exit_code: int | None = None
    finished_ms: int | None = None
    pid: int | None = None
    lines: deque[tuple[int, str, str]] = field(init=False)
    next_seq: int = 0
    cancel_requested: bool = False
    redact: tuple[str, ...] = ()
    pending_file: str | None = None
    file_id: str | None = None
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.lines = deque(maxlen=self.buffer_lines)

    async def add(self, stream: str, raw: bytes) -> None:
        async with self.changed:
            line = clean_line(raw)
            for host_path in self.redact:
                line = line.replace(host_path, "<export>")
            self.lines.append((self.next_seq, stream, line))
            self.next_seq += 1
            self.changed.notify_all()

    def summary(self) -> dict[str, Any]:
        end = self.finished_ms if self.finished_ms is not None else _now_ms()
        out: dict[str, Any] = {
            "job_id": self.id,
            "cmd": self.cmd,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_ms": self.started_ms,
            "duration_ms": end - self.started_ms,
            "lines": self.next_seq,
        }
        if self.file_id is not None:
            out["file_id"] = self.file_id
        return out


async def _pump(job: Job, reader: asyncio.StreamReader, stream: str) -> None:
    """Split `reader` into lines for `job`; a line over LINE_CAP is cut and its rest dropped."""
    buf = bytearray()
    dropping = False
    while chunk := await reader.read(4096):
        buf += chunk
        while (end := buf.find(b"\n")) >= 0:
            line = bytes(buf[:end])
            del buf[: end + 1]
            if dropping:
                dropping = False
                continue
            await job.add(stream, line)
        if len(buf) > LINE_CAP and not dropping:
            await job.add(stream, bytes(buf))
            dropping = True
        if dropping:
            buf.clear()
    if buf and not dropping:
        await job.add(stream, bytes(buf))


class StartArgs(Args):
    cmd: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")
    args: dict[str, Any] = Field(default_factory=dict)


class JobRef(Args):
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class StreamArgs(JobRef):
    from_seq: int = Field(default=0, ge=0)


class AuditArgs(Args):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class JobManager:
    """Runs and tracks jobs for the registered services (`wolfbet` -> WolfBetService, ...)."""

    def __init__(
        self,
        buffer_lines: int = BUFFER_LINES,
        cancel_grace_s: float = CANCEL_GRACE_S,
        files: FileStore | None = None,
    ) -> None:
        self.files = files
        self.services: dict[str, JobService] = {}
        self.jobs: dict[str, Job] = {}
        self.audit_log: deque[dict[str, Any]] = deque(maxlen=AUDIT_RECORDS)
        self._audit_seq = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self.buffer_lines = buffer_lines
        self.cancel_grace_s = cancel_grace_s

    def register(self, prefix: str, service: JobService) -> None:
        self.services[prefix] = service

    def mutating_running(self, prefix: str) -> bool:
        return any(
            j.service == prefix and j.mutating and j.status == "running" for j in self.jobs.values()
        )

    # ── lifecycle ──
    async def start(self, cmd: str, raw_args: dict[str, Any]) -> Job:
        prefix, _, name = cmd.partition(".")
        service = self.services.get(prefix)
        if service is None or not service.long_running(name):
            raise InvalidArgs("job.start takes a long-running command")
        prepared = service.prepare(name, raw_args)
        if prepared.mutating and self.mutating_running(prefix):
            raise Busy(f"a mutating {prefix} job is running")
        self._prune()
        peer = current_peer.get()
        job = Job(
            id=uuid.uuid4().hex,
            service=prefix,
            cmd=cmd,
            args=prepared.args,
            peer_uid=peer.uid if peer else None,
            mutating=prepared.mutating,
            started_ms=_now_ms(),
            buffer_lines=self.buffer_lines,
            redact=prepared.redact,
            pending_file=prepared.file_id,
        )
        try:
            proc = await spawn(prepared.argv, prepared.cwd)
        except OSError:
            self._discard_file(job)
            raise
        job.pid = proc.pid
        self.jobs[job.id] = job
        self._audit("start", job)
        log.info("job %s started: %s (uid=%s)", job.id, cmd, job.peer_uid)
        task = asyncio.create_task(self._watch(job, proc, prepared.timeout_s, service, name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _watch(
        self,
        job: Job,
        proc: asyncio.subprocess.Process,
        timeout_s: float,
        service: JobService,
        name: str,
    ) -> None:
        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError("job process has no output pipes")
        pumps = asyncio.gather(_pump(job, proc.stdout, "stdout"), _pump(job, proc.stderr, "stderr"))
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            timed_out = True
            self._signal(job, signal.SIGKILL)
            await proc.wait()
        try:
            await asyncio.wait_for(pumps, 5)
        except Exception as e:
            log.warning("job %s: output not fully read (%s)", job.id, type(e).__name__)
        job.exit_code = proc.returncode
        job.finished_ms = _now_ms()
        if job.cancel_requested:
            job.status = "cancelled"
        elif timed_out:
            job.status = "timeout"
        else:
            job.status = "succeeded" if proc.returncode == 0 else "failed"
        try:
            service.after_job(name)
        except Exception:
            log.exception("after_job failed for %s", job.cmd)
        if job.pending_file is not None and self.files is not None:
            stored = self.files.seal(job.pending_file, ok=job.status == "succeeded")
            job.file_id = stored.file_id if stored else None
        self._audit("finish", job)
        log.info("job %s %s (exit %s)", job.id, job.status, job.exit_code)
        job.done.set()
        async with job.changed:
            job.changed.notify_all()

    def _discard_file(self, job: Job) -> None:
        if job.pending_file is not None and self.files is not None:
            self.files.discard(job.pending_file)

    def _signal(self, job: Job, sig: signal.Signals) -> None:
        if job.pid is None:
            return
        with contextlib.suppress(ProcessLookupError):  # the group may have just exited
            os.killpg(job.pid, sig)

    async def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status != "running":
            return job
        job.cancel_requested = True
        self._signal(job, signal.SIGTERM)
        try:
            await asyncio.wait_for(job.done.wait(), self.cancel_grace_s)
        except TimeoutError:
            self._signal(job, signal.SIGKILL)
            await job.done.wait()
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFound(job_id)
        return job

    async def stream(self, job_id: str, from_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
        """Lines from `from_seq` on (or the oldest kept), live until the job has finished."""
        job = self.get(job_id)
        next_seq = from_seq
        while True:
            async with job.changed:
                batch = [item for item in job.lines if item[0] >= next_seq]
                if not batch:
                    if job.done.is_set():
                        return
                    await job.changed.wait()
                    continue
            for seq, stream, line in batch:
                yield {"seq": seq, "stream": stream, "line": line}
                next_seq = seq + 1

    def _prune(self) -> None:
        cutoff = _now_ms() - KEEP_FINISHED_S * 1000
        finished = sorted(
            (j for j in self.jobs.values() if j.status != "running"),
            key=lambda j: j.finished_ms or 0,
        )
        for job in finished:
            if (job.finished_ms or 0) < cutoff or len(self.jobs) >= MAX_JOBS:
                del self.jobs[job.id]

    # ── audit ──
    def record(self, event: str, **fields: Any) -> None:
        """Append one audit record (seq, event, `fields`, at_ms) for the api to pull."""
        self._audit_seq += 1
        self.audit_log.append(
            {"seq": self._audit_seq, "event": event, **fields, "at_ms": _now_ms()}
        )

    def _audit(self, event: str, job: Job) -> None:
        fields: dict[str, Any] = {
            "job_id": job.id,
            "cmd": job.cmd,
            "args": job.args,
            "peer_uid": job.peer_uid,
        }
        if event == "finish":
            fields.update(
                status=job.status, exit_code=job.exit_code, duration_ms=job.summary()["duration_ms"]
            )
        self.record(event, **fields)

    def audit(self, after: int = 0, limit: int = 100) -> dict[str, Any]:
        records = [r for r in self.audit_log if r["seq"] > after][:limit]
        return {"records": records, "next": records[-1]["seq"] if records else after}

    # ── protocol ops ──
    def handlers(self) -> dict[str, Handler]:
        async def job_start(raw: dict[str, Any]) -> dict[str, Any]:
            a = parse_args(StartArgs, raw)
            return {"job_id": (await self.start(a.cmd, a.args)).id}

        async def job_get(raw: dict[str, Any]) -> dict[str, Any]:
            return self.get(parse_args(JobRef, raw).job_id).summary()

        async def job_stream(raw: dict[str, Any]) -> Streaming:
            a = parse_args(StreamArgs, raw)
            job = self.get(a.job_id)

            async def final() -> dict[str, Any]:
                return job.summary()

            return Streaming(frames=self.stream(a.job_id, a.from_seq), final=final)

        async def job_cancel(raw: dict[str, Any]) -> dict[str, Any]:
            return (await self.cancel(parse_args(JobRef, raw).job_id)).summary()

        async def job_audit(raw: dict[str, Any]) -> dict[str, Any]:
            a = parse_args(AuditArgs, raw)
            return self.audit(a.after, a.limit)

        return {
            "job.start": job_start,
            "job.get": job_get,
            "job.stream": job_stream,
            "job.cancel": job_cancel,
            "job.audit": job_audit,
        }
