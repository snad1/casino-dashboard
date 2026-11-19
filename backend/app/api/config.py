"""The bot's config as a form: its schema, current values and a dry-run diff of edits.

Every route needs `config.write` on the service. The agent reads the schema and the saved
config, and runs the dry run; nothing here writes. Saving goes through the `config_set`
control action, which is idempotent, stepped up and audited. Only keys the schema lists
are returned, so the api key and anything else the bot keeps never reach the browser.
"""

import asyncio
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, JsonValue, ValidationError

from app.adapters.wolfbet.control import ConfigChanges, config_pairs
from app.agent_client import AgentError
from app.api.control import AGENT_DOWN, CONTROLS, NOT_CONTROLLABLE, ServiceName, agent_or_error
from app.auth.models import User
from app.auth.permissions import Perm, require

CONFIG_UNREADABLE = "Could not read the bot's config. Try again."
INVALID_VALUE = "One of the values is not valid."
_BOT_PREFIX = "Mutation failed: "

log = structlog.get_logger("app.config")
router = APIRouter(tags=["config"])
Writer = Annotated[User, Depends(require(Perm.CONFIG_WRITE))]


class ConfigKey(BaseModel):
    key: str
    type: Literal["number", "integer", "boolean", "string"]
    help: str | None = None
    choices: list[str] | None = None
    min: float | None = None
    max: float | None = None
    off_allowed: bool
    format: str | None = None
    default: JsonValue = None
    live_reload: bool
    lane_overridable: bool


class LaneRange(BaseModel):
    min: int
    max: int


class ConfigOut(BaseModel):
    keys: list[ConfigKey]
    lane_count: int
    override_lanes: LaneRange
    split_choices: list[str]
    lane_split: str
    base: dict[str, JsonValue]
    lanes: dict[str, dict[str, JsonValue]]


class DiffRow(BaseModel):
    key: str
    old: JsonValue
    new: JsonValue
    apply: Literal["live", "restart"]
    removed: bool = False


class PreviewOut(BaseModel):
    base: list[DiffRow]
    lanes: dict[str, list[DiffRow]]
    warnings: list[str]


def _known_service(service: str) -> None:
    if service not in CONTROLS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)


def _document(data: dict[str, Any]) -> dict[str, Any] | None:
    doc = data.get("json")
    return doc if isinstance(doc, dict) else None


def _agent_failed(error: AgentError) -> HTTPException:
    log.warning("config call failed at the agent", code=error.code)
    if error.code == "bad_request":
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INVALID_VALUE)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN)


@router.get("/{service}/config", response_model=ConfigOut)
async def get_config(service: ServiceName, request: Request, _: Writer) -> ConfigOut:
    """The settable keys with their rules, and the saved base and per-lane values."""
    _known_service(service)
    agent = agent_or_error(request)
    try:
        schema_data, show_data = await asyncio.gather(
            agent.call(f"{service}.config_schema"), agent.call(f"{service}.config_show")
        )
    except AgentError as error:
        raise _agent_failed(error) from None
    schema, show = _document(schema_data), _document(show_data)
    if schema is None or show is None or "error" in schema or "error" in show:
        log.warning("config could not be read", schema=schema is not None, show=show is not None)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, CONFIG_UNREADABLE)
    try:
        keys = [ConfigKey.model_validate(entry) for entry in schema["keys"]]
        names = {k.key for k in keys}
        lanes = schema["lanes"]
        return ConfigOut(
            keys=keys,
            lane_count=show["lanes"],
            override_lanes=LaneRange.model_validate(lanes["override_lanes"]),
            split_choices=lanes["split"]["choices"],
            lane_split=show["lane_split"],
            base={k: v for k, v in show["config"].items() if k in names},
            lanes={
                lane: {k: v for k, v in values.items() if k in names}
                for lane, values in show["lane_overrides"].items()
            },
        )
    except (KeyError, TypeError, AttributeError, ValidationError):
        log.warning("config reply did not match the schema")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, CONFIG_UNREADABLE) from None


@router.post("/{service}/config/preview", response_model=PreviewOut)
async def preview_config(
    service: ServiceName, body: ConfigChanges, request: Request, _: Writer
) -> PreviewOut:
    """What saving `body` would change, checked by the bot as a real save is; writes nothing."""
    _known_service(service)
    try:
        data = await agent_or_error(request).call(f"{service}.config_dry_run", config_pairs(body))
    except AgentError as error:
        raise _agent_failed(error) from None
    doc = _document(data)
    refused = doc.get("error") if doc else None
    if isinstance(refused, dict) and isinstance(refused.get("message"), str):
        message = refused["message"].removeprefix(_BOT_PREFIX)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message[:300])
    if doc is None or data.get("exit_code") != 0:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN)
    try:
        return PreviewOut.model_validate(doc)
    except ValidationError:
        log.warning("dry run reply did not match the schema")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN) from None
