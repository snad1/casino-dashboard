"""casino-agent serve | ping | version."""

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
import sys
import time
from collections.abc import Mapping
from typing import Any

from agent import __version__
from agent.config import AgentConfig, ConfigError, load_config
from agent.files import FileStore
from agent.jobs import JobManager
from agent.metrics import DEFAULT_PROJECT, Metrics
from agent.protocol import PROTO_VERSION, signed_request
from agent.pty import PtyManager
from agent.server import DEFAULT_HANDLERS, AgentServer
from agent.services.common import BotService
from agent.services.registry import SERVICES, configured
from agent.services.wolfbet_tg import WolfBetTgPaths, WolfBetTgService

log = logging.getLogger("agent.cli")


def call(
    config: AgentConfig, cmd: str, args: dict[str, Any] | None = None, timeout: float = 5.0
) -> dict[str, Any]:
    """Send one signed request to the running agent and return its parsed response."""
    now_ms = int(time.time() * 1000)
    request = signed_request(config.token, secrets.token_hex(16), cmd, now_ms, args)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(config.socket_path))
        sock.sendall(json.dumps(request).encode() + b"\n")
        reply = sock.makefile("rb").readline()
    if not reply:
        raise ConnectionError("the agent closed the connection without a reply")
    result: dict[str, Any] = json.loads(reply)
    return result


def installed_services(
    env: Mapping[str, str], jobs: JobManager, files: FileStore | None
) -> list[BotService]:
    """Each configured bot whose ctl is installed, its jobs registered; a missing bot is
    logged and left out, so only its own commands are off."""
    out = []
    for name in configured(env):

        def busy(name: str = name) -> bool:
            return jobs.mutating_running(name)

        service = SERVICES[name].build(env, busy, files)
        ctl = service.paths.ctl
        if not (ctl.is_file() and os.access(ctl, os.X_OK)):
            log.warning("%s: %s not found; its commands are off", name, ctl)
            continue
        jobs.register(name, service)
        out.append(service)
    return out


async def _serve(config: AgentConfig) -> None:
    """The agent with each installed bot's commands, WolfBet's Telegram bot's per-user
    commands, jobs, the export store (swept now and every 10 min) and the owner's terminal."""
    env = os.environ
    names = configured(env)
    files = FileStore(SERVICES[names[0]].build(env, lambda: False, None).paths.export_dir)
    files.sweep()
    jobs = JobManager(files=files)
    services = installed_services(env, jobs, files)
    terminal = PtyManager.from_env(audit=jobs.record)
    metrics = Metrics(
        project=env.get("CASINO_AGENT_COMPOSE_PROJECT", DEFAULT_PROJECT),
        units=tuple(SERVICES[s.name].unit for s in services),
    )
    handlers = {
        **DEFAULT_HANDLERS,
        **{name: h for service in services for name, h in service.handlers().items()},
        **(WolfBetTgService(WolfBetTgPaths.from_env()).handlers() if "wolfbet" in names else {}),
        **jobs.handlers(),
        **files.handlers(),
        **terminal.handlers(),
        "metrics": metrics.handler,
    }
    sweeper = asyncio.create_task(files.sweep_forever())
    try:
        await AgentServer(config, handlers=handlers).serve_forever()
    finally:
        sweeper.cancel()
        await terminal.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casino-agent", description=__doc__)
    parser.add_argument("command", choices=("serve", "ping", "version"))
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"casino-agent {__version__} (proto {PROTO_VERSION})")
        return 0
    try:
        config = load_config()
    except ConfigError as e:
        print(f"casino-agent: {e}", file=sys.stderr)
        return 2
    if args.command == "serve":
        try:
            # Every configured bot's paths are checked before anything starts.
            for name in configured(os.environ):
                SERVICES[name].build(os.environ, lambda: False, None)
        except ValueError as e:
            print(f"casino-agent: {e}", file=sys.stderr)
            return 2
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        try:
            asyncio.run(_serve(config))
        except KeyboardInterrupt:
            return 0
        return 0
    try:
        pong = call(config, "ping")
        version = call(config, "version")
    except (OSError, ValueError) as e:
        print(
            f"casino-agent: cannot reach the agent at {config.socket_path} ({type(e).__name__})",
            file=sys.stderr,
        )
        return 1
    if not (pong.get("ok") and version.get("ok")):
        code = (pong.get("error") or version.get("error") or {}).get("code", "unknown")
        print(f"casino-agent: refused ({code})", file=sys.stderr)
        return 1
    data = version["data"]
    print(f"pong (proto {data['proto']}, agent {data['agent']})")
    return 0
