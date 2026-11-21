"""Fixtures for the Telegram user tests: an api with a bot link secret, and Telegram users."""

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.settings import Settings
from tests.tg_links import SECRET, TgUsers


@pytest.fixture
def tg_settings(control_settings: Settings) -> Settings:
    return control_settings.model_copy(update={"tg_login_secret": SecretStr(SECRET)})


@pytest.fixture
def tg_users(tg_settings: Settings, migrated: Settings) -> Iterator[TgUsers]:
    users = TgUsers(tg_settings, migrated)
    yield users
    users.close()
