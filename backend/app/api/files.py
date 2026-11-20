"""Download the file an export job wrote, streamed from the host agent.

The job must be one the user may see (`job_access`), finished and carrying a `file_id`.
The browser gets a fixed, safe name; the host path and the agent's own name never reach it.
"""

import base64
import binascii
from collections.abc import AsyncIterator
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.agent_client import AgentClient, AgentError, StreamEnd
from app.api.control import (
    AGENT_DOWN,
    JOB_NOT_FOUND,
    Db,
    JobId,
    ServiceName,
    agent_or_error,
    job_call,
)
from app.auth.models import User
from app.auth.permissions import Perm, require

FILE_NOT_READY = "The export has no file yet. Wait for it to finish."
MEDIA_TYPES = {"csv": "text/csv; charset=utf-8", "json": "application/json"}

log = structlog.get_logger("app.files")
router = APIRouter(tags=["files"])


async def _chunks(
    first: bytes, rest: AsyncIterator[dict[str, object] | StreamEnd]
) -> AsyncIterator[bytes]:
    yield first
    try:
        async for item in rest:
            if isinstance(item, StreamEnd):
                return
            yield _decode(item)
    except (AgentError, ValueError) as error:
        log.warning("export download cut short", error=type(error).__name__)


def _decode(item: dict[str, object]) -> bytes:
    data = item.get("data")
    if not isinstance(data, str):
        raise ValueError("chunk without data")
    try:
        return base64.b64decode(data, validate=True)
    except binascii.Error:
        raise ValueError("chunk is not base64") from None


@router.get("/{service}/jobs/{job_id}/file")
async def download_job_file(
    service: ServiceName,
    job_id: JobId,
    request: Request,
    user: Annotated[User, Depends(require(Perm.EXPORT))],
    db: Db,
    format: Annotated[Literal["csv", "json"], Query()] = "csv",
) -> StreamingResponse:
    """The export's file as an attachment named `<service>-export-<job>.<format>`."""
    summary = await job_call(request, db, user, service, job_id, "job.get")
    file_id = summary.get("file_id")
    if summary.get("status") != "succeeded" or not isinstance(file_id, str):
        raise HTTPException(status.HTTP_404_NOT_FOUND, FILE_NOT_READY)
    agent: AgentClient = agent_or_error(request)
    stream = agent.stream("file.read", {"file_id": file_id})
    try:
        first = await anext(stream)
        if isinstance(first, StreamEnd):
            first_bytes = b""
        else:
            first_bytes = _decode(first)
    except AgentError as error:
        log.warning("export download failed at the agent", code=error.code)
        code = (
            status.HTTP_404_NOT_FOUND if error.code == "not_found" else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(code, JOB_NOT_FOUND if code == 404 else AGENT_DOWN) from None
    except (StopAsyncIteration, ValueError):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN) from None
    name = f"{service}-export-{job_id[:8]}.{format}"
    return StreamingResponse(
        _chunks(first_bytes, stream),
        media_type=MEDIA_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
