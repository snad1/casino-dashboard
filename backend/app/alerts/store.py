"""Read rules and state, and record a firing with its deliveries and new state at once.

The caller commits, so a firing, its three deliveries and the state that says it fired land
together or not at all. A rule whose saved params no longer pass their checks is skipped
and logged, never evaluated.
"""

from datetime import datetime
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.models import CHANNELS, AlertDelivery, AlertEvent, AlertRule, AlertRuleState
from app.alerts.rules import INGEST_KINDS, Firing, Rule

log = structlog.get_logger("app.alerts.store")

State = dict[str, Any]


async def load(
    db: AsyncSession, service: str, *, live: bool
) -> tuple[list[Rule], dict[tuple[int, int], State]]:
    """The service's enabled rules (live ones, or the ingest ones) and their saved state."""
    ingest = [k.value for k in INGEST_KINDS]
    kind_filter = AlertRule.kind.not_in(ingest) if live else AlertRule.kind.in_(ingest)
    rows = await db.scalars(
        select(AlertRule)
        .where(AlertRule.service == service, AlertRule.enabled.is_(True), kind_filter)
        .order_by(AlertRule.id)
    )
    rules: list[Rule] = []
    for row in rows:
        try:
            rules.append(
                Rule.build(
                    id=row.id,
                    service=row.service,
                    lane=row.lane,
                    kind=row.kind,
                    name=row.name,
                    params=row.params,
                    cooldown_s=row.cooldown_s,
                )
            )
        except (ValueError, ValidationError):
            log.warning("alert rule skipped: its settings are not valid", rule=row.id)
    states: dict[tuple[int, int], State] = {}
    if rules:
        saved = await db.scalars(
            select(AlertRuleState).where(AlertRuleState.rule_id.in_([r.id for r in rules]))
        )
        states = {(s.rule_id, s.lane): dict(s.state) for s in saved}
    return rules, states


async def save_states(db: AsyncSession, states: dict[tuple[int, int], State]) -> None:
    for (rule_id, lane), state in states.items():
        statement = insert(AlertRuleState).values(rule_id=rule_id, lane=lane, state=state)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[AlertRuleState.rule_id, AlertRuleState.lane],
                set_={"state": statement.excluded.state, "updated_at": text("now()")},
            )
        )


async def record(db: AsyncSession, service: str, fired: Firing, key_lane: int, state: State) -> int:
    """Insert the event, one pending delivery per channel, and the rule's new state."""
    event = AlertEvent(
        rule_id=fired.rule_id,
        service=service,
        lane=fired.lane,
        kind=fired.kind,
        value=fired.value,
        message=fired.message[:500],
    )
    db.add(event)
    await db.flush()
    db.add_all(AlertDelivery(event_id=event.id, channel=channel) for channel in CHANNELS)
    await save_states(db, {(fired.rule_id, key_lane): state})
    return event.id


async def day_profit(db: AsyncSession, service: str, start: datetime) -> float:
    """Profit since `start` (host-local midnight) from the hourly rollup."""
    value = await db.scalar(
        text(
            "SELECT coalesce(sum(profit), 0) FROM bets_1h"
            " WHERE service = :service AND bucket >= :start"
        ),
        {"service": service, "start": start},
    )
    return float(value or 0.0)
