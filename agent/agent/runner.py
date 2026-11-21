"""Run one allowlisted program: argv only, a scrubbed environment, a hard timeout."""

import asyncio
import contextlib
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
STDOUT_TAIL = 64 * 1024
STDERR_TAIL = 16 * 1024


class CommandTimeout(Exception):
    """The program ran past its timeout and was killed with its process group."""


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


def scrubbed_env(env: Mapping[str, str]) -> dict[str, str]:
    """Only what the bots' tools need to find binaries, HOME and the user's systemd.

    HOME and XDG_RUNTIME_DIR are filled in when the agent's own environment lacks them (a
    system unit gets neither): wolfctl stops on an unset HOME, and `systemctl --user` cannot
    reach the user's manager without XDG_RUNTIME_DIR.
    """
    out = {k: env[k] for k in ENV_KEYS if k in env}
    out.setdefault("HOME", str(Path.home()))
    out.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return out


def tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


async def spawn(
    argv: Sequence[str], cwd: Path | None = None, env: Mapping[str, str] = os.environ
) -> asyncio.subprocess.Process:
    """Start `argv` (argv[0] an absolute path): no shell, no stdin, scrubbed env, own session."""
    if not argv or not os.path.isabs(argv[0]):
        raise ValueError("argv[0] must be an absolute path")
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=scrubbed_env(env),
        start_new_session=True,
    )


async def run(
    argv: Sequence[str],
    timeout_s: float,
    cwd: Path | None = None,
    env: Mapping[str, str] = os.environ,
) -> RunResult:
    """Run `argv` to the end and capture its output (see spawn).

    Raises CommandTimeout after `timeout_s` seconds; the whole process group is killed.
    """
    proc = await spawn(argv, cwd, env)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):  # it may have just exited
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise CommandTimeout(f"{os.path.basename(argv[0])} ran over {timeout_s:g} s") from None
    return RunResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=out.decode("utf-8", "replace"),
        stderr=err.decode("utf-8", "replace"),
    )
