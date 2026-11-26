"""Step-up: risky routes need a code checked on this session and used within the last day."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiSession, User
from app.auth.ratelimit import check_code_rate
from app.auth.sessions import TOUCH_AFTER, current_user
from app.auth.totp import BAD_CODE, verify_code
from app.db.engine import get_session

STEP_UP_NEEDED = "Confirm with your authenticator code to continue."
STEP_UP_IDLE = timedelta(hours=24)
# Without a limit, daily use would never ask again; a stolen session would keep step-up for good.
STEP_UP_MAX = timedelta(days=7)


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


async def is_fresh(db: AsyncSession, session: ApiSession) -> bool:
    """Whether the session's code still covers a protected action; using it extends the cover."""
    if session.step_up_at is None:
        return False
    now = datetime.now(UTC)
    last_used = max(session.step_up_at, session.step_up_used_at or session.step_up_at)
    if now - session.step_up_at > STEP_UP_MAX or now - last_used > STEP_UP_IDLE:
        return False
    if now - last_used > TOUCH_AFTER:
        session.step_up_used_at = now
        await db.commit()
    return True


def require_fresh() -> Callable[..., Awaitable[User]]:
    """Dependency for destructive actions, config writes and the terminal."""

    async def dependency(
        user: Annotated[User, Depends(current_user)],
        request: Request,
        db: Annotated[AsyncSession, Depends(get_session)],
    ) -> User:
        if not await is_fresh(db, request.state.api_session):
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
    session.step_up_used_at = None
    await db.commit()
