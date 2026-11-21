"""Every route the api serves, from its own schema: who may call it without signing in, and
that a signed-in browser cannot be made to change anything without the session's CSRF token.

A new route fails here until it is either protected or added to PUBLIC on purpose.
"""

import re
from typing import Any

from app.audit import UNSAFE_METHODS
from app.auth.csrf import CSRF_FAILED, LOGIN_PATHS
from app.auth.models import Role
from app.main import create_app
from app.settings import Settings
from tests.conftest import make_client

pytest_plugins = ["tests.fake_agent", "tests.control_rig"]

PARAM = re.compile(r"\{([^}]+)\}")
# Each of these proves who the caller is by its own token (invite, reset link, bot link,
# password) or, for logout, only ends whatever session the request carries.
PUBLIC = {
    ("GET", "/healthz"),
    ("POST", "/auth/login"),
    ("POST", "/auth/telegram"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/invites/accept"),
    ("POST", "/auth/password-reset"),
    ("POST", "/auth/password-reset/confirm"),
}


def _routes(settings: Settings) -> list[tuple[str, str]]:
    paths: dict[str, dict[str, Any]] = create_app(settings).openapi()["paths"]
    return sorted((method.upper(), path) for path, ops in paths.items() for method in ops)


def _url(path: str) -> str:
    names = {"service": "wolfbet", "uid": "42"}
    return PARAM.sub(lambda m: names.get(m.group(1), "x"), path)


def test_every_route_but_the_public_ones_refuses_anonymous_callers(
    app_settings: Settings,
) -> None:
    open_routes = []
    with make_client(app_settings) as client:
        for method, path in _routes(app_settings):
            status = client.request(method, _url(path), json={}).status_code
            if status not in (401, 403):
                open_routes.append((method, path, status))
    assert {(m, p) for m, p, _ in open_routes} == PUBLIC, open_routes


def test_a_signed_in_browser_changes_nothing_without_the_csrf_token(
    control_settings: Settings, signed_in_as: Any
) -> None:
    client = signed_in_as(Role.OWNER)
    unsafe = [
        (m, p) for m, p in _routes(control_settings) if m in UNSAFE_METHODS and p not in LOGIN_PATHS
    ]
    assert any(p.startswith("/{service}/control/") for _, p in unsafe)
    for method, path in unsafe:
        response = client.request(method, _url(path), json={})
        assert response.status_code == 403, (method, path, response.status_code)
        assert response.json() == {"detail": CSRF_FAILED}, (method, path)
