"""casino-agent serve | ping | version."""

import argparse
import asyncio
import json
import logging
import secrets
import socket
import sys
import time
from typing import Any

from agent import __version__
from agent.config import AgentConfig, ConfigError, load_config
from agent.jobs import JobManager
from agent.protocol import PROTO_VERSION, signed_request
from agent.server import DEFAULT_HANDLERS, AgentServer
from agent.services.wolfbet import WolfBetPaths, WolfBetService


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
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        try:
            jobs = JobManager()
            service = WolfBetService(
                WolfBetPaths.from_env(), busy=lambda: jobs.mutating_running("wolfbet")
            )
            jobs.register("wolfbet", service)
            handlers = {**DEFAULT_HANDLERS, **service.handlers(), **jobs.handlers()}
            asyncio.run(AgentServer(config, handlers=handlers).serve_forever())
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
