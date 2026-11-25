"""Casino API keys in the bot host's vault: listed, added, rotated and deleted, never read back.

Every route needs `keys.manage` on the service (owner or admin, with 2FA), or `keys.own` (a
lane manager, who sees and touches only the keys they added); the writes also need a step-up
in the last 5 minutes. A key travels to the agent once and reaches the bot on
its stdin; no answer, log or audit row carries it (the audit middleware redacts the
`secret` field). A key's id is its label as a slug, so a retried add finds the key it made
instead of storing a second copy. The bot decides which lanes use a key and refuses to
delete one in use. The Telegram bot token is one entry of kind `telegram`, always stored as
`telegram`, that only the owner may add, rotate or delete.
"""

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_client import AgentError
from app.api.control import AGENT_DOWN, CONTROLS, NOT_CONTROLLABLE, ServiceName, agent_or_error
from app.auth.key_lanes import KEY_LANES
from app.auth.models import Invite, Role, User, UserRole
from app.auth.permissions import Perm, denial, roles_for
from app.auth.sessions import current_user
from app.auth.stepup import require_fresh
from app.db.engine import get_session
from app.db.models import Service

KEY_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,31}$"
VAULT_DOWN = "The key vault on the bot host can't be used. Check the host."
BAD_LABEL = "Use at least one letter or digit in the key's name."
INVALID_KEY = "The key must be 8-4096 printable characters with no spaces."
TG_KEY_ID = "telegram"
TG_OWNER_ONLY = "Only the owner can change the Telegram bot token."
BAD_TG_TOKEN = "That doesn't look like a Telegram bot token."  # noqa: S105  # a message, not a secret
_TG_TOKEN = re.compile(r"\d{5,12}:[A-Za-z0-9_-]{30,64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SECRET = re.compile(r"[\x21-\x7e]{8,4096}")
_REFUSALS = {
    "key_exists": status.HTTP_409_CONFLICT,
    "key_in_use": status.HTTP_409_CONFLICT,
    "key_unknown": status.HTTP_404_NOT_FOUND,
    "invalid_secret": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_argument": status.HTTP_422_UNPROCESSABLE_CONTENT,
}

log = structlog.get_logger("app.keys")
router = APIRouter(tags=["keys"])
Db = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class KeyAccess:
    """Who asks, and whether they manage every key or only their own."""

    user: User
    every_key: bool
    owner: bool = False

    def check_telegram(self, key_id: str) -> None:
        """Refuse anyone but the owner a change to the Telegram bot token."""
        if key_id == TG_KEY_ID and not self.owner:
            raise HTTPException(status.HTTP_403_FORBIDDEN, TG_OWNER_ONLY)


async def key_access(
    service: ServiceName, user: Annotated[User, Depends(current_user)], db: Db
) -> KeyAccess:
    """`keys.manage` opens every key; `keys.own` (a lane manager) only the caller's own."""
    if await denial(db, user, service, Perm.KEYS_MANAGE) is None:
        owner = Role.OWNER in await roles_for(db, user.id, None)
        return KeyAccess(user, every_key=True, owner=owner)
    detail = await denial(db, user, service, Perm.KEYS_OWN)
    if detail is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
    return KeyAccess(user, every_key=False)


Manager = Annotated[KeyAccess, Depends(key_access)]
Fresh = Annotated[User, Depends(require_fresh())]
KeyId = Annotated[str, Path(pattern=KEY_ID_PATTERN)]


class KeyOut(BaseModel):
    id: str
    label: str
    kind: Literal["key", "proxy", "telegram"]
    last4: str
    owner_user_id: int | None
    created_at: str
    rotated_at: str | None
    used_by_lanes: list[int]


class KeysOut(BaseModel):
    keys: list[KeyOut]


class KeyWriteOut(BaseModel):
    key: KeyOut
    note: str | None


class RotateIn(BaseModel):
    """A key's new secret: write-only, never stored by the dashboard."""

    model_config = ConfigDict(extra="forbid")

    secret: str = Field(max_length=4096, json_schema_extra={"writeOnly": True})

    @field_validator("secret")
    @classmethod
    def _secret(cls, value: str) -> str:
        if not _SECRET.fullmatch(value):
            raise ValueError(INVALID_KEY)
        return value


class KeyIn(RotateIn):
    """A new vault entry: a casino `key`, a `proxy` URL that carries a password, or the
    Telegram bot token."""

    label: str = Field(min_length=1, max_length=64)
    kind: Literal["key", "proxy", "telegram"] = "key"

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        if _CONTROL.search(value) or value != value.strip():
            raise ValueError("must be printable, without leading or trailing spaces")
        return value


def key_id_for(label: str) -> str:
    """`Friend A` -> `friend-a`: the vault id a label is stored under."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:32].rstrip("-")
    if not re.fullmatch(KEY_ID_PATTERN, slug):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_LABEL)
    return slug


def _known(service: str) -> None:
    if service not in CONTROLS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)


async def _call(request: Request, service: str, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
    """The bot's JSON answer; its refusals become 404/409/422 with its own message."""
    try:
        data = await agent_or_error(request).call(f"{service}.{cmd}", args)
    except AgentError as error:
        log.warning("key call failed at the agent", cmd=cmd, code=error.code)
        if error.code == "bad_request":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INVALID_KEY) from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN) from None
    doc = data.get("json")
    if not isinstance(doc, dict):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN)
    refused = doc.get("error")
    if isinstance(refused, dict):
        code = refused.get("code")
        message = refused.get("message")
        if code in _REFUSALS and isinstance(message, str):
            raise HTTPException(_REFUSALS[code], message[:200])
        log.warning("the bot refused a key call", cmd=cmd, code=code)
        detail = VAULT_DOWN if code == "vault_unavailable" else AGENT_DOWN
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail)
    if data.get("exit_code") != 0:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN)
    return doc


async def _check_owner(request: Request, service: str, access: KeyAccess, key_id: str) -> None:
    """A user limited to their own keys gets 404 for any other key, as if it did not exist."""
    if access.every_key:
        return
    doc = await _call(request, service, "key_list", {})
    for row in doc.get("keys", []):
        if isinstance(row, dict) and row.get("id") == key_id:
            if row.get("owner_user_id") == access.user.id:
                return
            break
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"No key named {key_id}.")


def _written(doc: dict[str, Any]) -> KeyWriteOut:
    return KeyWriteOut(key=KeyOut.model_validate(doc["key"]), note=doc.get("note"))


@router.get("/{service}/keys", response_model=KeysOut)
async def list_keys(
    service: ServiceName, request: Request, access: Manager, mine: bool = Query(default=False)
) -> KeysOut:
    """Every key with its label, last 4 characters, owner and the lanes that use it."""
    _known(service)
    doc = await _call(request, service, "key_list", {})
    keys = [KeyOut.model_validate(row) for row in doc.get("keys", [])]
    only_mine = mine or not access.every_key
    return KeysOut(keys=[k for k in keys if not only_mine or k.owner_user_id == access.user.id])


@router.post("/{service}/keys", response_model=KeyWriteOut, status_code=201)
async def add_key(
    service: ServiceName, body: KeyIn, request: Request, access: Manager, _: Fresh
) -> KeyWriteOut:
    """Store a new key under its label's slug, owned by the caller; the Telegram bot token
    always goes under `telegram`."""
    _known(service)
    key_id = TG_KEY_ID if body.kind == "telegram" else key_id_for(body.label)
    access.check_telegram(key_id)
    if body.kind == "telegram" and not _TG_TOKEN.fullmatch(body.secret):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_TG_TOKEN)
    args = {
        "key_id": key_id,
        "label": body.label,
        "secret": body.secret,
        "owner_user_id": access.user.id,
        "kind": body.kind,
    }
    return _written(await _call(request, service, "key_put", args))


@router.post("/{service}/keys/{key_id}/rotate", response_model=KeyWriteOut)
async def rotate_key(
    service: ServiceName,
    key_id: KeyId,
    body: RotateIn,
    request: Request,
    access: Manager,
    _: Fresh,
) -> KeyWriteOut:
    """Replace a key's secret; lanes using it pick the new one up when they restart."""
    _known(service)
    access.check_telegram(key_id)
    await _check_owner(request, service, access, key_id)
    args = {"key_id": key_id, "secret": body.secret}
    return _written(await _call(request, service, "key_rotate", args))


@router.delete("/{service}/keys/{key_id}", status_code=204, response_class=Response)
async def delete_key(
    service: ServiceName, key_id: KeyId, request: Request, access: Manager, _: Fresh, db: Db
) -> Response:
    """Delete a key no lane uses, with every role and pending invite on it: a key added
    later under the same id must not open to them."""
    _known(service)
    access.check_telegram(key_id)
    await _check_owner(request, service, access, key_id)
    await _call(request, service, "key_delete", {"key_id": key_id})
    service_id = select(Service.id).where(Service.name == service).scalar_subquery()
    await db.execute(
        delete(UserRole).where(UserRole.service_id == service_id, UserRole.key_ref == key_id)
    )
    await db.execute(
        delete(Invite).where(
            Invite.service_id == service_id,
            Invite.key_refs.contains([key_id]),
            Invite.accepted_at.is_(None),
        )
    )
    await db.commit()
    KEY_LANES.forget(service)
    return Response(status_code=204)
