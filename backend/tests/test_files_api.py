"""An export's file downloads as an attachment with a fixed name, only for who may see the job."""

import base64
import secrets
from typing import Any

from app.api.control import AGENT_DOWN
from app.api.files import FILE_NOT_READY
from app.auth.models import Role
from app.settings import Settings
from tests.conftest import Account
from tests.control_rig import set_role
from tests.fake_agent import JOB_ID, FakeAgent, default_reply, fail, job_summary, ok

FILE_ID = "fe" * 16
CONTENT = b"id,profit\n1,0.5\n2,-0.1\n" * 4000


def _agent_with_file(status: str = "succeeded") -> Any:
    def reply(request: dict[str, Any]) -> list[dict[str, Any]]:
        if request["cmd"] == "job.get":
            summary = {**job_summary(status, 0 if status == "succeeded" else None)}
            if status == "succeeded":
                summary["file_id"] = FILE_ID
            return [ok(request, summary)]
        if request["cmd"] == "file.read":
            pieces = [CONTENT[i : i + 65536] for i in range(0, len(CONTENT), 65536)]
            frames = [
                {"id": request["id"], "seq": n, "data": base64.b64encode(piece).decode()}
                for n, piece in enumerate(pieces)
            ]
            end = {"file_id": FILE_ID, "name": "wolfbet-sessions.csv", "bytes": len(CONTENT)}
            return [*frames, ok(request, end)]
        return default_reply(request)

    return reply


def _export(client: Any) -> str:
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": secrets.token_urlsafe(24)}
    response = client.post("/wolfbet/control/export", json={"format": "csv"}, headers=headers)
    assert response.status_code == 202
    job_id: str = response.json()["job_id"]
    return job_id


def test_the_file_downloads_whole_as_an_attachment_with_a_fixed_name(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    fake_agent.reply = _agent_with_file()
    client = signed_in_as(Role.OPERATOR)
    job_id = _export(client)
    response = client.get(f"/wolfbet/jobs/{job_id}/file?format=csv")
    assert response.status_code == 200
    assert response.content == CONTENT
    assert response.headers["content-disposition"] == (
        f'attachment; filename="wolfbet-export-{JOB_ID[:8]}.csv"'
    )
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    read = next(call for call in fake_agent.calls if call["cmd"] == "file.read")
    assert read["args"] == {"file_id": FILE_ID}
    assert "wolfbet-sessions.csv" not in response.headers["content-disposition"]


def test_a_running_job_or_a_job_without_a_file_has_nothing_to_download(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    fake_agent.reply = _agent_with_file("running")
    client = signed_in_as(Role.OPERATOR)
    job_id = _export(client)
    response = client.get(f"/wolfbet/jobs/{job_id}/file")
    assert (response.status_code, response.json()) == (404, {"detail": FILE_NOT_READY})
    assert "file.read" not in fake_agent.cmds()


def test_others_and_unknown_jobs_get_not_found(
    signed_in_as: Any, fake_agent: FakeAgent, control_settings: Settings, account: Account
) -> None:
    fake_agent.reply = _agent_with_file()
    client = signed_in_as(Role.OPERATOR)
    job_id = _export(client)
    assert client.get(f"/wolfbet/jobs/{'cd' * 16}/file").status_code == 404
    assert client.get(f"/wolfbet/jobs/{job_id}/file?format=exe").status_code == 422
    set_role(control_settings, account.id, Role.VIEWER)
    assert client.get(f"/wolfbet/jobs/{job_id}/file").status_code == 403


def test_an_agent_failure_before_the_file_starts_is_a_502(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.OPERATOR)
    job_id = _export(client)
    base = _agent_with_file()
    fake_agent.reply = lambda request: (
        [fail(request, "internal")] if request["cmd"] == "file.read" else base(request)
    )
    response = client.get(f"/wolfbet/jobs/{job_id}/file")
    assert (response.status_code, response.json()) == (502, {"detail": AGENT_DOWN})
