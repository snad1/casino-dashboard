"""The owner's terminal on the host: one recorded PTY session at a time.

`pty.open` starts tmux (session `casino-web`, reattached when it already exists) running
the account's login zsh, or bash when there is no zsh, in a new PTY with a fixed
environment. The api types with `pty.write`, follows the output with `pty.stream` and
resizes with `pty.resize`. A session ends on `pty.close`, after 10 min without input,
after 60 min, when the shell exits or when its recording reaches its cap. Ending hangs up
the tmux client only, so the shell and its jobs stay in tmux for the next open.

Everything typed and printed is written as asciicast v2 to
`<state>/recordings/<session_id>.cast` (mode 0600) and read back with `pty.recording`;
the open and the end of a session go to the audit trail the api pulls with `job.audit`.
"""

import asyncio
import base64
import binascii
import codecs
import contextlib
import fcntl
import json
import logging
import os
import pwd
import shutil
import signal
import stat
import struct
import termios
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agent.errors import Busy, NotFound
from agent.peercred import current_peer
from agent.protocol import Chunk, Streaming
from agent.runner import scrubbed_env
from agent.validators import Args, InvalidArgs, parse_args

log = logging.getLogger("casino-agent")

TMUX_SESSION = "casino-web"
TERM = "xterm-256color"
IDLE_S = 600.0
MAX_S = 3600.0
FRAME_BYTES = 16 * 1024
FLUSH_S = 0.02
BACKLOG_BYTES = 1024 * 1024
WRITE_BYTES = 16 * 1024
WRITE_TIMEOUT_S = 5.0
RECORDING_BYTES = 64 * 1024 * 1024
RECORDING_CHUNK = 64 * 1024
HANGUP_GRACE_S = 2.0
KILL_WAIT_S = 5.0
REAP_POLL_S = 0.05

Reason = Literal["closed", "idle", "max", "exited", "recording_full", "shutdown"]
Audit = Callable[..., None]
Handler = Callable[[dict[str, Any]], Awaitable[Any]]
Which = Callable[..., str | None]


@dataclass(frozen=True)
class Launch:
    """What a session runs: an argv (argv[0] absolute) and the shell it ends up in."""

    argv: list[str]
    shell: str


def _account() -> pwd.struct_passwd | None:
    try:
        return pwd.getpwuid(os.getuid())
    except KeyError:
        return None


def default_launch(
    env: Mapping[str, str] = os.environ,
    which: Which = shutil.which,
    account_shell: str | None = None,
) -> Launch:
    """tmux running the login zsh (else zsh or bash from PATH); without tmux, the shell alone."""
    path = env.get("PATH", os.defpath)
    if account_shell is None:
        account = _account()
        account_shell = account.pw_shell if account else ""
    shell = account_shell
    if os.path.basename(shell) != "zsh" or not os.path.isabs(shell):
        shell = which("zsh", path=path) or which("bash", path=path) or "/bin/bash"
    tmux = which("tmux", path=path)
    if tmux is None:
        log.warning("tmux not found: the terminal runs %s without a lasting session", shell)
        return Launch([shell, "-l"], shell)
    return Launch([tmux, "new-session", "-A", "-s", TMUX_SESSION, shell, "-l"], shell)


def terminal_env(shell: str, env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The scrubbed agent env plus what a login shell expects; no agent settings or secrets."""
    out = scrubbed_env(env)
    account = _account()
    user = account.pw_name if account else env.get("USER", "")
    out.update(TERM=TERM, SHELL=shell)
    if user:
        out.update(USER=user, LOGNAME=user)
    return out


def _winsize(cols: int, rows: int) -> bytes:
    return struct.pack("HHHH", rows, cols, 0, 0)


def _spawn(argv: Sequence[str], env: Mapping[str, str], cols: int, rows: int) -> tuple[int, int]:
    """Fork `argv` on a new PTY as a session leader with it as controlling tty: (pid, master)."""
    if not argv or not os.path.isabs(argv[0]):
        raise ValueError("argv[0] must be an absolute path")
    master, slave = os.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, _winsize(cols, rows))
        pid = os.fork()
    except BaseException:
        os.close(master)
        os.close(slave)
        raise
    if pid == 0:
        try:
            os.close(master)
            os.login_tty(slave)
            os.execve(argv[0], list(argv), dict(env))  # noqa: S606 - argv list, no shell
        finally:
            os._exit(127)
    os.close(slave)
    os.set_blocking(master, False)
    return pid, master


class Recording:
    """An asciicast v2 file written line by line, so it stays valid if the agent dies."""

    def __init__(
        self, path: Path, cols: int, rows: int, shell: str, cap: int = RECORDING_BYTES
    ) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        self._file = os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8", buffering=1)
        self._start = time.monotonic()
        self._out = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._in = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.cap = cap
        self.bytes = 0
        self.full = False
        header = {
            "version": 2,
            "width": cols,
            "height": rows,
            "timestamp": int(time.time()),
            "env": {"TERM": TERM, "SHELL": shell},
        }
        self._line(json.dumps(header))

    def output(self, data: bytes) -> None:
        self._event("o", self._out.decode(data))

    def input(self, data: bytes) -> None:
        self._event("i", self._in.decode(data))

    def resize(self, cols: int, rows: int) -> None:
        self._event("r", f"{cols}x{rows}")

    def close(self) -> None:
        self._event("o", self._out.decode(b"", final=True))
        self._file.close()

    def _event(self, code: str, text: str) -> None:
        if text:
            self._line(json.dumps([round(time.monotonic() - self._start, 6), code, text]))

    def _line(self, line: str) -> None:
        if self.full or self._file.closed:
            return
        self._file.write(line + "\n")
        self.bytes += len(line) + 1
        self.full = self.bytes >= self.cap


@dataclass
class Session:
    id: str
    pid: int
    fd: int
    cols: int
    rows: int
    peer_uid: int | None
    recording: Recording
    started: float = field(default_factory=time.monotonic)
    started_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    last_input: float = field(default_factory=time.monotonic)
    chunks: deque[tuple[int, bytes]] = field(default_factory=deque)
    backlog: int = 0
    next_seq: int = 0
    exit_code: int | None = None
    reason: Reason | None = None
    finished_ms: int | None = None
    watchdog: asyncio.Task[None] | None = None
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def add(self, data: bytes, backlog_cap: int) -> None:
        for start in range(0, len(data), FRAME_BYTES):
            piece = data[start : start + FRAME_BYTES]
            self.chunks.append((self.next_seq, piece))
            self.next_seq += 1
            self.backlog += len(piece)
        while self.backlog > backlog_cap and len(self.chunks) > 1:
            self.backlog -= len(self.chunks.popleft()[1])
        self.wake.set()

    def summary(self) -> dict[str, Any]:
        end = self.finished_ms if self.finished_ms is not None else int(time.time() * 1000)
        return {
            "session_id": self.id,
            "reason": self.reason,
            "started_ms": self.started_ms,
            "duration_ms": end - self.started_ms,
        }


class OpenArgs(Args):
    cols: int = Field(ge=1, le=500)
    rows: int = Field(ge=1, le=200)


class SessionRef(Args):
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class StreamArgs(SessionRef):
    from_seq: int = Field(default=0, ge=0)


class WriteArgs(SessionRef):
    data: str = Field(min_length=1, max_length=4 * ((WRITE_BYTES + 2) // 3))


class ResizeArgs(SessionRef, OpenArgs):
    pass


class PtyManager:
    """Owns the single terminal session: spawn, input, output, size, timers, recording, audit."""

    def __init__(
        self,
        recordings_dir: Path,
        audit: Audit,
        launch: Callable[[], Launch] = default_launch,
        idle_s: float = IDLE_S,
        max_s: float = MAX_S,
        flush_s: float = FLUSH_S,
        backlog_bytes: int = BACKLOG_BYTES,
        recording_bytes: int = RECORDING_BYTES,
        hangup_grace_s: float = HANGUP_GRACE_S,
    ) -> None:
        self.recordings_dir = recordings_dir
        self.audit = audit
        self.launch = launch
        self.idle_s = idle_s
        self.max_s = max_s
        self.flush_s = flush_s
        self.backlog_bytes = backlog_bytes
        self.recording_bytes = recording_bytes
        self.hangup_grace_s = hangup_grace_s
        self.session: Session | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def from_env(cls, audit: Audit, env: Mapping[str, str] = os.environ) -> "PtyManager":
        home = Path(env.get("HOME") or Path.home())
        state = Path(env.get("STATE_DIRECTORY") or home / ".local/state/casino-agent")
        return cls(state / "recordings", audit)

    def _ensure_dir(self) -> None:
        self.recordings_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.recordings_dir.is_symlink() or not self.recordings_dir.is_dir():
            raise RuntimeError(f"recordings dir {self.recordings_dir} is not a directory")
        os.chmod(self.recordings_dir, 0o700)

    # ── lifecycle ──
    def open(self, cols: int, rows: int) -> Session:
        if self.session is not None and not self.session.done.is_set():
            raise Busy("a terminal session is open")
        launch = self.launch()
        self._ensure_dir()
        session_id = uuid.uuid4().hex
        path = self.recordings_dir / f"{session_id}.cast"
        recording = Recording(path, cols, rows, launch.shell, self.recording_bytes)
        try:
            pid, fd = _spawn(launch.argv, terminal_env(launch.shell), cols, rows)
        except Exception:
            recording.close()
            path.unlink(missing_ok=True)
            raise
        peer = current_peer.get()
        s = Session(session_id, pid, fd, cols, rows, peer.uid if peer else None, recording)
        self.session = s
        asyncio.get_running_loop().add_reader(fd, self._on_output, s)
        s.watchdog = asyncio.create_task(self._watch(s))
        self.audit("pty.open", session_id=s.id, peer_uid=s.peer_uid, cols=cols, rows=rows)
        log.info("terminal %s opened (uid=%s, %dx%d)", s.id, s.peer_uid, cols, rows)
        return s

    def get(self, session_id: str) -> Session:
        s = self.session
        if s is None or s.id != session_id:
            raise NotFound(session_id)
        return s

    def _live(self, session_id: str) -> Session:
        s = self.get(session_id)
        if s.reason is not None:
            raise NotFound(session_id)
        return s

    def _on_output(self, s: Session) -> None:
        try:
            data = os.read(s.fd, FRAME_BYTES)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._end_soon(s, "exited")
            return
        s.add(data, self.backlog_bytes)
        s.recording.output(data)
        if s.recording.full:
            self._end_soon(s, "recording_full")

    def _end_soon(self, s: Session, reason: Reason) -> None:
        asyncio.get_running_loop().remove_reader(s.fd)
        task = asyncio.create_task(self._end(s, reason))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _watch(self, s: Session) -> None:
        tick = min(1.0, self.idle_s / 4, self.max_s / 4)
        while True:
            await asyncio.sleep(tick)
            now = time.monotonic()
            reason: Reason
            if self._reaped(s):
                reason = "exited"
            elif now - s.started >= self.max_s:
                reason = "max"
            elif now - s.last_input >= self.idle_s:
                reason = "idle"
            else:
                continue
            await self._end(s, reason)
            return

    async def close(self, session_id: str, reason: Reason = "closed") -> Session:
        s = self.get(session_id)
        await self._end(s, reason)
        return s

    async def shutdown(self) -> None:
        if self.session is not None:
            await self._end(self.session, "shutdown")

    async def _end(self, s: Session, reason: Reason) -> None:
        if s.reason is not None:
            await s.done.wait()
            return
        s.reason = reason
        asyncio.get_running_loop().remove_reader(s.fd)
        if s.watchdog is not None and s.watchdog is not asyncio.current_task():
            s.watchdog.cancel()
        # Closing the master hangs up the tty; an exiting child can block until its
        # pending output is drained, so the master goes before the wait.
        os.close(s.fd)
        if not self._reaped(s):
            self._signal(s, signal.SIGHUP)
            if not await self._reap(s, self.hangup_grace_s):
                self._signal(s, signal.SIGKILL)
                if not await self._reap(s, KILL_WAIT_S):
                    log.warning("terminal %s: pid %d did not exit after SIGKILL", s.id, s.pid)
        s.recording.close()
        s.finished_ms = int(time.time() * 1000)
        summary = s.summary()
        self.audit(
            "pty.close",
            session_id=s.id,
            peer_uid=s.peer_uid,
            cols=s.cols,
            rows=s.rows,
            reason=reason,
            duration_ms=summary["duration_ms"],
        )
        log.info("terminal %s closed: %s after %d ms", s.id, reason, summary["duration_ms"])
        s.done.set()
        s.wake.set()

    def _reaped(self, s: Session) -> bool:
        if s.exit_code is not None:
            return True
        try:
            pid, status = os.waitpid(s.pid, os.WNOHANG)
        except ChildProcessError:
            s.exit_code = -1
            return True
        if pid == 0:
            return False
        s.exit_code = os.waitstatus_to_exitcode(status)
        return True

    async def _reap(self, s: Session, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while not self._reaped(s):
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(REAP_POLL_S)
        return True

    @staticmethod
    def _signal(s: Session, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError):  # it may have just exited
            os.kill(s.pid, sig)

    # ── input, size, output ──
    async def write(self, session_id: str, data: bytes) -> None:
        s = self._live(session_id)
        s.last_input = time.monotonic()
        async with s.write_lock:
            s.recording.input(data)
            view = memoryview(data)
            deadline = time.monotonic() + WRITE_TIMEOUT_S
            while view:
                try:
                    view = view[os.write(s.fd, view) :]
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Busy("the terminal is not reading its input") from None
                    await asyncio.sleep(REAP_POLL_S)
                except OSError:
                    raise NotFound(session_id) from None

    def resize(self, session_id: str, cols: int, rows: int) -> Session:
        s = self._live(session_id)
        fcntl.ioctl(s.fd, termios.TIOCSWINSZ, _winsize(cols, rows))
        s.cols, s.rows = cols, rows
        s.last_input = time.monotonic()
        s.recording.resize(cols, rows)
        return s

    async def stream(self, session_id: str, from_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
        """Output from `from_seq` on (or the oldest kept), at most FRAME_BYTES per frame and
        one frame per `flush_s`, until the session has ended. A frame's seq is its last
        chunk's, so a client resumes from seq + 1."""
        s = self.get(session_id)
        next_seq = from_seq
        while True:
            frame = bytearray()
            last = -1
            for seq, data in s.chunks:
                if seq < next_seq:
                    continue
                if frame and len(frame) + len(data) > FRAME_BYTES:
                    break
                frame += data
                last = seq
            if not frame:
                if s.done.is_set():
                    return
                s.wake.clear()
                await s.wake.wait()
            else:
                next_seq = last + 1
                yield {"seq": last, "data": base64.b64encode(frame).decode()}
            await asyncio.sleep(self.flush_s)

    # ── recordings ──
    def _open_recording(self, session_id: str) -> int:
        """A read-only fd of a session's recording: a regular file in the dir, never a link."""
        if self.recordings_dir.is_symlink():
            raise NotFound(session_id)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(self.recordings_dir / f"{session_id}.cast", flags)
        except OSError:
            raise NotFound(session_id) from None
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise NotFound(session_id)
        return fd

    def recording(self, session_id: str) -> Streaming:
        """A session's asciicast file as chunks (also while it is still being written)."""
        os.close(self._open_recording(session_id))
        sent = 0

        async def frames() -> AsyncIterator[dict[str, Any]]:
            nonlocal sent
            try:
                fd = self._open_recording(session_id)
            except NotFound:
                return
            try:
                seq = 0
                while data := os.read(fd, RECORDING_CHUNK):
                    yield {"seq": seq, "data": base64.b64encode(data).decode()}
                    sent += len(data)
                    seq += 1
            finally:
                os.close(fd)

        async def final() -> dict[str, Any]:
            return {"session_id": session_id, "bytes": sent}

        return Streaming(frames=frames(), final=final, frame=Chunk)

    # ── protocol ops ──
    def handlers(self) -> dict[str, Handler]:
        async def pty_open(raw: dict[str, Any]) -> dict[str, Any]:
            a = parse_args(OpenArgs, raw)
            return {"session_id": self.open(a.cols, a.rows).id}

        async def pty_stream(raw: dict[str, Any]) -> Streaming:
            a = parse_args(StreamArgs, raw)
            s = self.get(a.session_id)

            async def final() -> dict[str, Any]:
                return s.summary()

            return Streaming(frames=self.stream(s.id, a.from_seq), final=final, frame=Chunk)

        async def pty_write(raw: dict[str, Any]) -> dict[str, Any]:
            a = parse_args(WriteArgs, raw)
            try:
                data = base64.b64decode(a.data, validate=True)
            except binascii.Error:
                raise InvalidArgs("invalid args: data") from None
            if not data or len(data) > WRITE_BYTES:
                raise InvalidArgs("invalid args: data")
            await self.write(a.session_id, data)
            return {}

        async def pty_resize(raw: dict[str, Any]) -> dict[str, Any]:
            a = parse_args(ResizeArgs, raw)
            s = self.resize(a.session_id, a.cols, a.rows)
            return {"cols": s.cols, "rows": s.rows}

        async def pty_close(raw: dict[str, Any]) -> dict[str, Any]:
            return (await self.close(parse_args(SessionRef, raw).session_id)).summary()

        async def pty_recording(raw: dict[str, Any]) -> Streaming:
            return self.recording(parse_args(SessionRef, raw).session_id)

        return {
            "pty.open": pty_open,
            "pty.stream": pty_stream,
            "pty.write": pty_write,
            "pty.resize": pty_resize,
            "pty.close": pty_close,
            "pty.recording": pty_recording,
        }
