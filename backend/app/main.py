"""FastAPI application factory."""

from typing import Literal

from fastapi import FastAPI, status
from pydantic import BaseModel


class Health(BaseModel):
    status: Literal["ok"]


def create_app() -> FastAPI:
    """Build the API app; interactive docs stay off until settings can gate them."""
    app = FastAPI(title="casino-dashboard", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz", response_model=Health, status_code=status.HTTP_200_OK)
    async def healthz() -> Health:
        return Health(status="ok")

    return app


app = create_app()
