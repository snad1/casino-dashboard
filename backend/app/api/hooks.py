"""GitHub's `workflow_run` webhook: a green CI run on main updates the bot, when the owner
switched auto-update on for it.

The request carries no session; GitHub signs the body with the shared webhook secret and
nothing is read from the body before that signature checks out. The audit middleware records
each call here with its outcome but not its body, so a payload (forged or not) never reaches
the audit table; the hook adds its own `control.update` row when it starts an update.
"""

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status

from app import registry
from app.agent_client import AgentError
from app.api.control import ServiceName, agent_or_error, run_action
from app.api.updates import auto_update_on, forget_check
from app.audit import AuditEntry, record
from app.logging import current_request_id
from app.settings import weak_secret_reason

router = APIRouter(tags=["hooks"])
log = structlog.get_logger("app.hooks")

SECRET_NAME = "github_webhook_secret"  # noqa: S105  # a name, not a secret
MAX_BODY = 1024 * 1024
NOT_FOUND = "Not found"
BAD_SIGNATURE = "Bad signature"


def load_secret(hooks_dir: Path) -> bytes | None:
    """The webhook secret, or None when it is not set up; a weak one stops the api."""
    path = hooks_dir / f"{SECRET_NAME}.txt"
    if not path.is_file():
        return None
    value = path.read_text().strip()
    reason = weak_secret_reason(SECRET_NAME, value)
    if reason is not None:
        raise ValueError(f"{SECRET_NAME} {reason}")
    return value.encode()


def _signed(secret: bytes, body: bytes, header: str | None) -> bool:
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return header is not None and hmac.compare_digest(expected, header)


def _green_run_on_main(event: str | None, payload: Any) -> str | None:
    """The commit of a completed, successful push run on main, else None."""
    if event != "workflow_run" or not isinstance(payload, dict):
        return None
    run = payload.get("workflow_run")
    if payload.get("action") != "completed" or not isinstance(run, dict):
        return None
    if (run.get("conclusion"), run.get("head_branch"), run.get("event")) != (
        "success",
        "main",
        "push",
    ):
        return None
    sha = run.get("head_sha")
    return sha if isinstance(sha, str) and sha else None


async def _body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Too large")
    return bytes(body)


@router.post("/hooks/github/{service}", status_code=status.HTTP_204_NO_CONTENT)
async def github_hook(service: ServiceName, request: Request) -> Response:
    secret: bytes | None = request.app.state.github_hook_secret
    if secret is None or not registry.is_enabled(service):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    body = await _body(request)
    if not _signed(secret, body, request.headers.get("x-hub-signature-256")):
        log.warning("github hook refused", svc=service)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_SIGNATURE)
    try:
        payload = json.loads(body)
    except ValueError:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    sha = _green_run_on_main(request.headers.get("x-github-event"), payload)
    handled: dict[str, set[str]] = request.app.state.hook_shas
    if sha is None or sha in handled.setdefault(service, set()):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    async with request.app.state.sessionmaker() as db:
        if not await auto_update_on(db, service):
            return Response(status_code=status.HTTP_204_NO_CONTENT)
    handled[service].add(sha)
    spec = (registry.controls(service) or {})["update"]
    try:
        _, out = await run_action(agent_or_error(request), service, spec, dict(spec.fixed_args))
        result = f"job:{out.job_id}"
        forget_check(request, service)
    except AgentError as error:
        log.warning("auto-update failed at the agent", svc=service, code=error.code)
        result = f"agent:{error.code}"
    await record(
        request.app.state.sessionmaker,
        AuditEntry(
            action="control.update",
            result=result,
            service=service,
            args={"trigger": "github", "sha": sha[:12]},
            request_id=current_request_id(),
        ),
    )
    accepted = (
        status.HTTP_202_ACCEPTED if result.startswith("job:") else status.HTTP_502_BAD_GATEWAY
    )
    return Response(status_code=accepted)
