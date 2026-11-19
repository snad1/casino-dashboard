"""Comments: viewers write on sessions, bets and the chart; only authors edit; moderators
delete; text is stored as sent; writes are rate limited and audited."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import AuditLog
from app.auth.models import Role, User
from app.auth.ratelimit import COMMENT_LIMIT, TOO_MANY_COMMENTS
from app.comments import EMPTY, NOT_YOURS, TARGET_NOT_FOUND, TOO_LONG
from app.settings import Settings
from tests.conftest import NO_LOGIN, make_user, new_email, run_in_session
from tests.test_history_api import T0, Seed

XSS = '<img src=x onerror="alert(1)"><script>alert(2)</script>'


@pytest.fixture
def seed(app_settings: Settings) -> Iterator[Seed]:
    data = Seed(app_settings)
    yield data
    data._run("DELETE FROM comments WHERE service = :s", {"s": data.service})
    data._run("DELETE FROM services WHERE name = :s", {"s": data.service})
    data.clean()


@pytest.fixture
def people(app_settings: Settings, as_user: Any) -> dict[str, TestClient]:
    def client(role: Role, two_factor: bool = False) -> TestClient:
        made: TestClient = as_user(make_user(app_settings, role, two_factor=two_factor))
        return made

    return {
        "ann": client(Role.VIEWER),
        "bob": client(Role.VIEWER),
        "admin": client(Role.ADMIN, two_factor=True),
    }


def _post(client: TestClient, service: str, **body: Any) -> Any:
    return client.post(f"/{service}/comments", json=body)


def test_viewers_comment_on_a_session_a_bet_and_the_chart(
    people: dict[str, TestClient], seed: Seed
) -> None:
    ann = people["ann"]
    epoch = seed.epoch()
    seed.session(epoch, 1)
    seed.bet(epoch, 5, T0 + timedelta(seconds=5))
    seed._run(
        "INSERT INTO services (name, adapter, host) VALUES (:s, 'wolfbet', 'h')",
        {"s": seed.service},
    )
    on_session = _post(ann, seed.service, kind="session", epoch_id=epoch, session_id=1, body=" hi ")
    assert on_session.status_code == 201
    body = on_session.json()
    assert (body["body"], body["can_edit"], body["can_delete"], body["edited_at"]) == (
        "hi",
        True,
        True,
        None,
    )
    assert "@" not in body["author"]
    assert (
        _post(ann, seed.service, kind="bet", epoch_id=epoch, bet_id=5, body="b").status_code == 201
    )
    note_at = (T0 + timedelta(minutes=2)).isoformat()
    assert _post(ann, seed.service, kind="chart", at=note_at, body="n").status_code == 201

    path = f"/{seed.service}/comments"
    session = (
        people["bob"]
        .get(path, params={"kind": "session", "epoch_id": epoch, "session_id": 1})
        .json()["items"]
    )
    assert [(c["body"], c["can_edit"], c["can_delete"]) for c in session] == [("hi", False, False)]
    bets = ann.get(path, params={"kind": "bet", "epoch_id": epoch, "bet_id": 5}).json()["items"]
    assert [c["body"] for c in bets] == ["b"]
    window = {"kind": "chart", "from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat()}
    notes = ann.get(path, params=window).json()["items"]
    assert [(c["body"], c["at"]) for c in notes] == [("n", note_at.replace("+00:00", "Z"))]
    too_wide = {**window, "to": (T0 + timedelta(days=40)).isoformat()}
    assert ann.get(path, params=too_wide).status_code == 422
    assert ann.get(path, params={"kind": "session"}).status_code == 422


def test_bad_bodies_and_missing_targets_are_refused(
    people: dict[str, TestClient], seed: Seed
) -> None:
    ann = people["ann"]
    epoch = seed.epoch()
    seed.session(epoch, 1)
    target = {"kind": "session", "epoch_id": epoch, "session_id": 1}
    blank = _post(ann, seed.service, **target, body="   ")
    assert (blank.status_code, blank.json()["detail"]) == (422, EMPTY)
    long = _post(ann, seed.service, **target, body="x" * 2001)
    assert (long.status_code, long.json()["detail"]) == (422, TOO_LONG)
    missing = _post(ann, seed.service, kind="session", epoch_id=epoch, session_id=99, body="x")
    assert (missing.status_code, missing.json()["detail"]) == (404, TARGET_NOT_FOUND)
    unknown_service = _post(ann, seed.service, kind="chart", at=T0.isoformat(), body="x")
    assert unknown_service.status_code == 404


def test_only_the_author_edits_and_a_moderator_may_delete(
    people: dict[str, TestClient], seed: Seed
) -> None:
    ann, bob, admin = people["ann"], people["bob"], people["admin"]
    epoch = seed.epoch()
    seed.session(epoch, 1)
    made = _post(ann, seed.service, kind="session", epoch_id=epoch, session_id=1, body="v1")
    path = f"/{seed.service}/comments/{made.json()['id']}"

    for response in (bob.patch(path, json={"body": "mine now"}), bob.delete(path)):
        assert (response.status_code, response.json()["detail"]) == (403, NOT_YOURS)
    assert admin.patch(path, json={"body": "admin text"}).status_code == 403
    edited = ann.patch(path, json={"body": "v2"})
    assert (edited.status_code, edited.json()["body"]) == (200, "v2")
    assert edited.json()["edited_at"] is not None

    other_service = f"/svc-other/comments/{made.json()['id']}"
    assert ann.patch(other_service, json={"body": "x"}).status_code in (403, 404)
    assert admin.delete(path).status_code == 204
    assert admin.delete(path).status_code == 404


def test_text_is_stored_as_sent_and_writes_are_audited(
    app_settings: Settings, people: dict[str, TestClient], seed: Seed
) -> None:
    ann = people["ann"]
    epoch = seed.epoch()
    seed.session(epoch, 1)
    made = _post(ann, seed.service, kind="session", epoch_id=epoch, session_id=1, body=XSS)
    assert made.json()["body"] == XSS

    async def audited(session: AsyncSession) -> list[str]:
        rows = await session.scalars(
            select(AuditLog.result).where(
                AuditLog.service == seed.service, AuditLog.action == "POST /{service}/comments"
            )
        )
        return list(rows)

    assert run_in_session(app_settings, audited) == ["201"]


def test_more_than_ten_writes_a_minute_are_refused(
    people: dict[str, TestClient], seed: Seed
) -> None:
    ann = people["ann"]
    epoch = seed.epoch()
    seed.session(epoch, 1)
    target = {"kind": "session", "epoch_id": epoch, "session_id": 1}
    codes = [
        _post(ann, seed.service, **target, body=str(i)).status_code for i in range(COMMENT_LIMIT)
    ]
    assert codes == [201] * COMMENT_LIMIT
    over = _post(ann, seed.service, **target, body="one more")
    assert (over.status_code, over.json()["detail"]) == (429, TOO_MANY_COMMENTS)


def test_roleless_users_can_neither_read_nor_write(
    app_settings: Settings, as_user: Any, seed: Seed
) -> None:
    async def roleless(session: AsyncSession) -> User:
        user = User(email=new_email(), password_hash=NO_LOGIN)
        session.add(user)
        await session.commit()
        return user

    nobody: TestClient = as_user(run_in_session(app_settings, roleless))
    path = f"/{seed.service}/comments"
    assert nobody.get(path, params={"kind": "session"}).status_code == 403
    assert (
        nobody.post(path, json={"kind": "chart", "at": T0.isoformat(), "body": "x"}).status_code
        == 403
    )
