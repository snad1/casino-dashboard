"""Typed checks for every value that may reach a command line.

A value passes only in a canonical form built from a small character set, so the
argv item it becomes can never be read as a flag, a path outside its place, or
more than one argument. Anything else raises InvalidArgs before a process starts.
"""

import math
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

LANE_ID_MAX = 99
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_KEY_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_KEY_LABEL = re.compile(r"[^\x00-\x1f\x7f]{1,64}")
_KEY_SECRET = re.compile(r"[\x21-\x7e]{8,4096}")
_LANE_NAME = re.compile(r"[A-Za-z0-9_]([A-Za-z0-9 _-]{0,30}[A-Za-z0-9_-])?")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")
_INT = re.compile(r"-?[0-9]{1,15}")
_NUMBER = re.compile(r"-?[0-9]{1,15}(\.[0-9]{1,15})?([eE]-?[0-9]{1,3})?")
_STREAK = re.compile(r"[0-9]{1,6}:[0-9]{1,9}(\.[0-9]{1,9})?")
_OFF = frozenset({"off", "none"})
_ON_WORDS = frozenset({"on", "true", "yes", "1"})
_OFF_WORDS = frozenset({"off", "false", "no", "0"})


class InvalidArgs(ValueError):
    """The request's arguments failed validation; nothing was run."""


class InvalidValue(InvalidArgs):
    """A config value the schema refuses; `key` is the schema's name for it, safe to show."""

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key = key


class Args(BaseModel):
    """Base for every command's arguments: closed, strictly typed, immutable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _iso_date(value: str) -> str:
    """'2026-11-01', '2026-11-01 14:00' or '2026-11-01T14:00:05' as ISO without timezone."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}([ T][0-9]{2}:[0-9]{2}(:[0-9]{2})?)?", value):
        raise ValueError("must be YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS]")
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    return parsed.date().isoformat() if len(value) == 10 else parsed.isoformat()


def _name(value: str) -> str:
    if not _NAME.fullmatch(value) or value.startswith(("-", ".")):
        raise ValueError("must be 1-64 letters, digits, '_', '.' or '-', not starting with - or .")
    return value


def _lane_name(value: str) -> str:
    if not _LANE_NAME.fullmatch(value):
        raise ValueError(
            "must be 1-32 letters, digits, space, '_' or '-', not starting with - or a space"
        )
    return value


def _key_id(value: str) -> str:
    if not _KEY_ID.fullmatch(value):
        raise ValueError("must be 1-32 lowercase letters, digits, '_' or '-'")
    return value


def _key_label(value: str) -> str:
    if not _KEY_LABEL.fullmatch(value) or value != value.strip():
        raise ValueError("must be 1-64 printable characters without leading or trailing spaces")
    return value


def _key_secret(value: str) -> str:
    if not _KEY_SECRET.fullmatch(value):
        raise ValueError("must be 8-4096 printable characters with no spaces")
    return value


def _branch(value: str) -> str:
    if (
        not _BRANCH.fullmatch(value)
        or value.startswith(("-", "/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "//" in value
        or "/." in value
    ):
        raise ValueError("is not a valid branch name")
    return value


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


Lane = Annotated[int, Field(ge=1, le=LANE_ID_MAX)]
LaneTarget = Lane | Literal["all"]
IsoDate = Annotated[str, Field(max_length=19), AfterValidator(_iso_date)]
Name = Annotated[str, AfterValidator(_name)]
LaneName = Annotated[str, AfterValidator(_lane_name)]
KeyId = Annotated[str, AfterValidator(_key_id)]
KeyLabel = Annotated[str, AfterValidator(_key_label)]
KeySecret = Annotated[str, Field(max_length=4096), AfterValidator(_key_secret)]
Branch = Annotated[str, AfterValidator(_branch)]
PositiveFloat = Annotated[float, Field(gt=0, le=1e12), AfterValidator(_finite)]


def parse_args(model: type[Args], raw: dict[str, Any]) -> Any:
    """Validate `raw` against `model`; any problem becomes InvalidArgs (no value is echoed)."""
    from pydantic import ValidationError

    try:
        return model.model_validate(raw)
    except ValidationError as e:
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in e.errors()})
        raise InvalidArgs(f"invalid args: {', '.join(fields) or 'body'}") from None


def config_value(entry: dict[str, Any], raw: str) -> str:
    """The canonical text of `raw` for config key `entry` (one item of --config-schema keys)."""
    limit = entry.get("max_length", 512) if entry.get("format") == "rules" else 512
    if not isinstance(raw, str) or len(raw) > limit:
        raise InvalidValue(entry["key"], f"value must be a string up to {limit} characters")
    value = raw.strip()
    if entry.get("off_allowed") and value.lower() in _OFF:
        return "off"
    kind = entry["type"]
    if kind == "boolean":
        if value.lower() in _ON_WORDS:
            return "on"
        if value.lower() in _OFF_WORDS:
            return "off"
        raise InvalidValue(entry["key"], "must be on or off")
    if "choices" in entry:
        if value not in entry["choices"]:
            raise InvalidValue(entry["key"], "must be one of the listed choices")
        return value
    if kind == "integer":
        if not _INT.fullmatch(value):
            raise InvalidValue(entry["key"], "must be a whole number")
        _check_range(entry, int(value))
        return str(int(value))
    if kind == "number":
        if not _NUMBER.fullmatch(value) or not math.isfinite(float(value)):
            raise InvalidValue(entry["key"], "must be a number")
        _check_range(entry, float(value))
        return value
    if entry.get("format") == "N:X":
        if not _STREAK.fullmatch(value):
            raise InvalidValue(entry["key"], "must be N:X")
        return value
    if entry.get("format") == "url":
        return _url(entry, value)
    if entry.get("format") == "rules":
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise InvalidValue(entry["key"], "must be one line without control characters")
        return value
    raise InvalidValue(entry["key"], "this key cannot be set from the dashboard")


def _check_range(entry: dict[str, Any], number: float) -> None:
    if entry.get("clamps"):
        return
    if "min" in entry and number < entry["min"]:
        raise InvalidValue(entry["key"], "below the minimum")
    if "max" in entry and number > entry["max"]:
        raise InvalidValue(entry["key"], "above the maximum")


def _url(entry: dict[str, Any], value: str) -> str:
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise InvalidValue(entry["key"], "must be a URL without spaces or control characters")
    try:
        parts = urlsplit(value)
        port_ok = parts.port is None or 0 < parts.port < 65536
    except ValueError:
        raise InvalidValue(entry["key"], "is not a valid URL") from None
    target = parts.path if parts.scheme == "unix" else parts.hostname
    if parts.scheme not in entry.get("schemes", ()) or not target or not port_ok:
        raise InvalidValue(entry["key"], "must be a URL with an allowed scheme and a host")
    return value
