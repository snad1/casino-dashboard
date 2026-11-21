"""docs/parity/old-web.md: every test it names exists, and every old web route and template
has a row."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs/parity/old-web.md"
WEB = REPO.parent / "wolfbet/web"
SHARED_AUTH = REPO.parent / "casino-shared/web/routes/auth_routes.py"
ROW = re.compile(r"^\| `((?:GET|POST|WS) [^`]+|templates/[^`]+)` \|")
PREFIX = re.compile(r'APIRouter\(prefix="([^"]*)"')
ROUTE = re.compile(r'@(?:router|app)\.(get|post|websocket)\("([^"]*)"')


def _rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in DOC.read_text().splitlines():
        match = ROW.match(line)
        if match:
            tests = line.rstrip(" |").rsplit("|", 1)[1]
            rows[match.group(1)] = re.findall(r"`([^`]+::[^`]+)`", tests)
    return rows


def _routes(path: Path) -> set[str]:
    source = path.read_text()
    prefix = PREFIX.search(source)
    found = set()
    for method, route in ROUTE.findall(source):
        verb = "WS" if method == "websocket" else method.upper()
        found.add(f"{verb} {(prefix.group(1) if prefix else '')}{route}")
    return found


def test_every_named_test_exists() -> None:
    rows = _rows()
    assert rows, "no rows found"
    for key, tests in rows.items():
        assert tests, key
        for ref in tests:
            path, name = ref.split("::", 1)
            assert re.search(rf"def {re.escape(name)}\(", (REPO / path).read_text()), (key, ref)


@pytest.mark.skipif(
    not (WEB.exists() and SHARED_AUTH.exists()),
    reason="wolfbet and casino-shared are not checked out next to this repo",
)
def test_every_old_route_and_template_has_exactly_one_row() -> None:
    routes = {r for f in (WEB / "routes").glob("*.py") for r in _routes(f)}
    routes |= _routes(WEB / "app.py") | _routes(SHARED_AUTH)
    templates = {str(p.relative_to(WEB)) for p in (WEB / "templates").rglob("*.html")}
    assert set(_rows()) == routes | templates
