"""docs/parity/wolfctl.md: every test it names exists, and every wolfctl command has a row."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs/parity/wolfctl.md"
WOLFCTL = REPO.parent / "wolfbet/wolfctl"
ROW = re.compile(r"^\| `([a-z-]+)` \|")


def _rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in DOC.read_text().splitlines():
        match = ROW.match(line)
        if match:
            tests = line.rstrip(" |").rsplit("|", 1)[1]
            rows[match.group(1)] = re.findall(r"`([^`]+::[^`]+)`", tests)
    return rows


def _dispatcher_commands(script: str) -> set[str]:
    block = script.split('case "${1:-}" in', 1)[1].split("esac", 1)[0]
    names = re.findall(r"^\s{4}([a-z][a-z|-]*(?:\|-{1,2}[a-z]+)*)\)", block, re.M)
    return {alt for name in names for alt in name.split("|") if not alt.startswith("-")}


def test_every_named_test_exists() -> None:
    rows = _rows()
    assert rows, "no command rows found"
    for command, tests in rows.items():
        for ref in tests:
            path, name = ref.split("::", 1)
            source = (REPO / path).read_text()
            if path.endswith(".py"):
                assert re.search(rf"def {re.escape(name)}\(", source), (command, ref)
            else:
                assert f"test('{name}'" in source or f"test(\n\t'{name}'" in source, (command, ref)


def test_every_row_but_help_names_a_test() -> None:
    assert [c for c, tests in _rows().items() if not tests] == ["help"]


@pytest.mark.skipif(not WOLFCTL.exists(), reason="wolfbet is not checked out next to this repo")
def test_every_wolfctl_command_has_exactly_one_row() -> None:
    assert set(_rows()) == _dispatcher_commands(WOLFCTL.read_text())
