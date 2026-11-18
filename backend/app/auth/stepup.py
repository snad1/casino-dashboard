"""Step-up: risky routes need a code checked in the last few minutes on this session."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiSession, User
from app.auth.ratelimit import check_code_rate
from app.auth.sessions import current_user
from app.auth.totp import BAD_CODE, verify_code
from app.db.engine import get_session

STEP_UP_NEEDED = "Confirm with your authenticator code to continue."


router = APIRouter(prefix="/auth", tags=["auth"])


class NextStepRequired(Exception):
    """A 4xx whose body also names the step the client must show, such as a code prompt."""

    def __init__(self, status_code: int, detail: str, flag: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.flag = flag


def install_step_handler(app: FastAPI) -> None:
    async def handle(_: Request, exc: NextStepRequired) -> JSONResponse:
        return JSONResponse({"detail": exc.detail, exc.flag: True}, status_code=exc.status_code)

    app.add_exception_handler(NextStepRequired, handle)  # type: ignore[arg-type]


def require_fresh(minutes: int = 5) -> Callable[..., Awaitable[User]]:
    """Dependency for destructive actions, config writes and the terminal."""

    async def dependency(user: Annotated[User, Depends(current_user)], request: Request) -> User:
        session: ApiSession = request.state.api_session
        cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
        if session.step_up_at is None or session.step_up_at < cutoff:
            raise NextStepRequired(status.HTTP_403_FORBIDDEN, STEP_UP_NEEDED, "step_up_required")
        return user

    return dependency


class StepUpIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


@router.post("/step-up", status_code=status.HTTP_204_NO_CONTENT)
async def step_up(
    body: StepUpIn,
    user: Annotated[User, Depends(current_user)],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await check_code_rate(request.app.state.redis, user.id)
    if not await verify_code(db, request.app.state.totp_key, user.id, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_CODE)
    session: ApiSession = request.state.api_session
    session.step_up_at = datetime.now(UTC)
    await db.commit()
