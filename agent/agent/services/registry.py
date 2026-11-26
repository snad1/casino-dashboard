"""The bots this agent can drive, and which of them the host runs (`CASINO_AGENT_SERVICES`)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agent.config import ConfigError
from agent.files import FileStore
from agent.services import wolfbet
from agent.services.common import BotService


@dataclass(frozen=True)
class ServiceModule:
    build: Callable[[Mapping[str, str], Callable[[], bool], FileStore | None], BotService]
    unit: str


SERVICES: dict[str, ServiceModule] = {
    "wolfbet": ServiceModule(wolfbet.build, wolfbet.UNIT),
}


def configured(env: Mapping[str, str]) -> list[str]:
    """The services named in `CASINO_AGENT_SERVICES` (default wolfbet), commas or spaces,
    a repeat counted once; an unknown name or an empty list stops the agent."""
    raw = env.get("CASINO_AGENT_SERVICES")
    names = list(dict.fromkeys((raw if raw is not None else "wolfbet").replace(",", " ").split()))
    if not names:
        raise ConfigError("CASINO_AGENT_SERVICES needs at least one service")
    unknown = [name for name in names if name not in SERVICES]
    if unknown:
        raise ConfigError(f"Unknown service in CASINO_AGENT_SERVICES: {', '.join(unknown)}")
    return names
