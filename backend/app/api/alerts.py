"""Alert rules, their history and test-fire per service, and each user's own channels.

Anyone with `view` on the service reads its rules and the alerts that fired; creating,
editing, deleting and test-firing need `alerts.manage` (owner or admin, with 2FA). Every
write is audited by the audit middleware, and the live engine reloads its rules right away.
A test alert goes only to the caller: it is written straight as their own deliveries, so the
mailer never fans it out to anyone else.
"""

import re
from datetime import UTC, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.wolfbet.control import LANES_MAX
from app.alerts.channels import LINK_TTL, issue_link_code
from app.alerts.models import AlertDelivery, AlertEvent, AlertRule, UserChannels
from app.alerts.rules import Kind, Rule
from app.api.control import CONTROLS, NOT_CONTROLLABLE, ServiceName
from app.auth.models import User
from app.auth.permissions import Perm, require
from app.auth.sessions import current_user
from app.db.engine import get_session

NAME_MAX = 80
TEST_KIND = "test"
RULE_NOT_FOUND = "Alert rule not found."
INVALID_SETTINGS = "These rule settings are not valid for this kind of alert."
NO_CHANNEL = "Turn on email or Telegram in your alert settings first."
NO_TELEGRAM = "Telegram alerts are not set up on this dashboard."
QUIET_BOTH = "Set both ends of quiet hours, or neither, and make them differ."
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

router = APIRouter(tags=["alerts"])
Viewer = Annotated[User, Depends(require(Perm.VIEW))]
Manager = Annotated[User, Depends(require(Perm.ALERTS_MANAGE))]
Me = Annotated[User, Depends(current_user)]
Db = Annotated[AsyncSession, Depends(get_session)]
RuleId = Annotated[int, Path(ge=1)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleIn(_Body):
    name: Annotated[str, Field(min_length=1, max_length=NAME_MAX)]
    kind: Kind
    lane: Annotated[int, Field(ge=1, le=LANES_MAX)] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    cooldown_s: Annotated[int, Field(ge=0, le=86400)] = 600
    enabled: bool = False

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if _CONTROL.search(value) or not value.strip():
            raise ValueError("must be plain text")
        return value.strip()


class RuleOut(BaseModel):
    id: int
    name: str
    kind: str
    lane: int | None
    params: dict[str, Any]
    cooldown_s: int
    enabled: bool


class TestOut(BaseModel):
    channels: list[Literal["email", "telegram"]]


class EventOut(BaseModel):
    id: int
    kind: str
    lane: int | None
    value: float | None
    message: str
    fired_at: datetime


class EventPage(BaseModel):
    items: list[EventOut]
    next_before: int | None


class ChannelsIn(_Body):
    email_enabled: bool
    telegram_enabled: bool
    quiet_start: time | None = None
    quiet_end: time | None = None


class ChannelsOut(BaseModel):
    email_enabled: bool
    telegram_enabled: bool
    telegram_linked: bool
    telegram_available: bool
    quiet_start: time | None
    quiet_end: time | None


class LinkOut(BaseModel):
    url: str
    expires_at: datetime


def _known(service: str) -> None:
    if service not in CONTROLS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)


def _checked(body: RuleIn, service: str) -> dict[str, Any]:
    """The params as the engine will read them, or a 422 that names no internals."""
    try:
        rule = Rule.build(
            id=0,
            service=service,
            lane=body.lane,
            kind=body.kind,
            name=body.name,
            params=body.params,
            cooldown_s=body.cooldown_s,
        )
    except (ValueError, ValidationError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INVALID_SETTINGS) from None
    params: dict[str, Any] = rule.params.model_dump(mode="json")
    return params


def _out(row: AlertRule) -> RuleOut:
    return RuleOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        lane=row.lane,
        params=row.params,
        cooldown_s=row.cooldown_s,
        enabled=row.enabled,
    )


async def _rule(db: AsyncSession, service: str, rule_id: int) -> AlertRule:
    row = await db.get(AlertRule, rule_id)
    if row is None or row.service != service:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RULE_NOT_FOUND)
    return row


async def _reload(request: Request, service: str) -> None:
    engine = getattr(request.app.state, "alerts", None)
    if engine is not None and engine.service == service:
        await engine.reload()


@router.get("/{service}/alerts/rules", response_model=list[RuleOut])
async def list_rules(service: ServiceName, _: Viewer, db: Db) -> list[RuleOut]:
    _known(service)
    rows = await db.scalars(
        select(AlertRule).where(AlertRule.service == service).order_by(AlertRule.id)
    )
    return [_out(row) for row in rows]


@router.post("/{service}/alerts/rules", response_model=RuleOut, status_code=201)
async def create_rule(
    service: ServiceName, body: RuleIn, request: Request, _: Manager, db: Db
) -> RuleOut:
    _known(service)
    params = _checked(body, service)
    row = AlertRule(
        service=service,
        lane=body.lane,
        kind=body.kind.value,
        name=body.name,
        params=params,
        cooldown_s=body.cooldown_s,
        enabled=body.enabled,
    )
    db.add(row)
    await db.commit()
    await _reload(request, service)
    return _out(row)


@router.put("/{service}/alerts/rules/{rule_id}", response_model=RuleOut)
async def update_rule(
    service: ServiceName, rule_id: RuleId, body: RuleIn, request: Request, _: Manager, db: Db
) -> RuleOut:
    _known(service)
    params = _checked(body, service)
    row = await _rule(db, service, rule_id)
    row.lane, row.kind, row.name, row.params = body.lane, body.kind.value, body.name, params
    row.cooldown_s, row.enabled, row.updated_at = body.cooldown_s, body.enabled, datetime.now(UTC)
    await db.commit()
    await _reload(request, service)
    return _out(row)


@router.delete("/{service}/alerts/rules/{rule_id}", status_code=204, response_class=Response)
async def delete_rule(
    service: ServiceName, rule_id: RuleId, request: Request, _: Manager, db: Db
) -> Response:
    _known(service)
    await db.delete(await _rule(db, service, rule_id))
    await db.commit()
    await _reload(request, service)
    return Response(status_code=204)


@router.post("/{service}/alerts/rules/{rule_id}/test", response_model=TestOut)
async def test_rule(service: ServiceName, rule_id: RuleId, user: Manager, db: Db) -> TestOut:
    """Send a test alert for the rule to the caller only, on their own channels."""
    _known(service)
    row = await _rule(db, service, rule_id)
    prefs = await db.get(UserChannels, user.id)
    channels: list[Literal["email", "telegram"]] = []
    if prefs is not None and prefs.email_enabled:
        channels.append("email")
    if prefs is not None and prefs.telegram_enabled and prefs.telegram_chat_id is not None:
        channels.append("telegram")
    if not channels:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_CHANNEL)
    event = AlertEvent(
        rule_id=row.id,
        service=service,
        lane=row.lane,
        kind=TEST_KIND,
        message=f"Test alert: {row.name}",
    )
    db.add(event)
    await db.flush()
    db.add_all(AlertDelivery(event_id=event.id, user_id=user.id, channel=c) for c in channels)
    await db.commit()
    return TestOut(channels=channels)


@router.get("/{service}/alerts/events", response_model=EventPage)
async def list_events(
    service: ServiceName,
    _: Viewer,
    db: Db,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EventPage:
    """Newest first; pass `next_before` back as `before` for the next page."""
    _known(service)
    query = (
        select(AlertEvent)
        .where(AlertEvent.service == service)
        .order_by(AlertEvent.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(AlertEvent.id < before)
    rows = list(await db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return EventPage(
        items=[
            EventOut(
                id=r.id,
                kind=r.kind,
                lane=r.lane,
                value=r.value,
                message=r.message,
                fired_at=r.fired_at,
            )
            for r in rows
        ],
        next_before=rows[-1].id if more else None,
    )


def _channels_out(request: Request, prefs: UserChannels | None) -> ChannelsOut:
    return ChannelsOut(
        email_enabled=bool(prefs and prefs.email_enabled),
        telegram_enabled=bool(prefs and prefs.telegram_enabled),
        telegram_linked=bool(prefs and prefs.telegram_chat_id is not None),
        telegram_available=request.app.state.telegram_bot_username is not None,
        quiet_start=prefs.quiet_start if prefs else None,
        quiet_end=prefs.quiet_end if prefs else None,
    )


@router.get("/alerts/me/channels", response_model=ChannelsOut)
async def get_my_channels(request: Request, user: Me, db: Db) -> ChannelsOut:
    return _channels_out(request, await db.get(UserChannels, user.id))


@router.put("/alerts/me/channels", response_model=ChannelsOut)
async def set_my_channels(request: Request, body: ChannelsIn, user: Me, db: Db) -> ChannelsOut:
    """Turn the caller's channels on or off and set their quiet hours (host time)."""
    start, end = body.quiet_start, body.quiet_end
    if (start is None) != (end is None) or (start is not None and start == end):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, QUIET_BOTH)
    prefs = await db.get(UserChannels, user.id)
    if prefs is None:
        prefs = UserChannels(user_id=user.id)
        db.add(prefs)
    prefs.email_enabled = body.email_enabled
    prefs.telegram_enabled = body.telegram_enabled and prefs.telegram_chat_id is not None
    prefs.quiet_start, prefs.quiet_end = start, end
    prefs.updated_at = datetime.now(UTC)
    await db.commit()
    return _channels_out(request, prefs)


@router.post("/alerts/me/telegram-link", response_model=LinkOut)
async def link_telegram(request: Request, user: Me, db: Db) -> LinkOut:
    """A ten-minute link that opens the alert bot with the caller's one-time code."""
    username: str | None = request.app.state.telegram_bot_username
    if username is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_TELEGRAM)
    now = datetime.now(UTC)
    code = await issue_link_code(db, user.id, now)
    await db.commit()
    return LinkOut(url=f"https://t.me/{username}?start={code}", expires_at=now + LINK_TTL)
