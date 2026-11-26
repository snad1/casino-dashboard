"""The casino services this dashboard knows, and the ones this deployment serves.

Every service-specific lookup (its live adapter, its control buttons, its display name) goes
through here, so enabling a service is a `DASHBOARD_SERVICES` entry instead of code spread
over the routes. `create_app` enables the configured services once at startup, the way
settings are loaded once.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.adapters.base import ControlAction, ServiceAdapter
from app.adapters.wolfbet import WolfBetAdapter
from app.adapters.wolfbet.control import ACTIONS as WOLFBET_ACTIONS


@dataclass(frozen=True)
class ServiceEntry:
    name: str
    label: str
    adapter: type[ServiceAdapter]
    controls: Mapping[str, ControlAction]


KNOWN: dict[str, ServiceEntry] = {
    "wolfbet": ServiceEntry("wolfbet", "WolfBet", WolfBetAdapter, WOLFBET_ACTIONS),
}

_enabled: tuple[str, ...] = ("wolfbet",)


def unknown(names: Iterable[str]) -> list[str]:
    """The names the registry does not know, in the order given."""
    return [name for name in names if name not in KNOWN]


def enable(names: Iterable[str]) -> None:
    """Serve exactly these services from now on; an unknown name is refused."""
    global _enabled
    names = tuple(names)
    bad = unknown(names)
    if bad:
        raise ValueError(f"Unknown service in DASHBOARD_SERVICES: {', '.join(bad)}")
    _enabled = names


def enabled() -> tuple[str, ...]:
    return _enabled


def is_enabled(name: str) -> bool:
    return name in _enabled


def controls(name: str) -> Mapping[str, ControlAction] | None:
    """The service's control buttons, or None when this deployment doesn't serve it."""
    return KNOWN[name].controls if name in _enabled else None


def label(name: str) -> str:
    """The display name: the registry's, or the capitalised name for a service it lacks."""
    entry = KNOWN.get(name)
    return entry.label if entry else name.capitalize()
