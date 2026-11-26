"""Hostile strings in any argument are refused or reach argv as one inert item.

For every string-typed argument of every WolfBet command, and for config keys and
values, hypothesis feeds shell metacharacters, newlines, NUL, `--flag` text, path
traversal and arbitrary unicode. Each value must either raise InvalidArgs (and run
nothing) or become exactly one argv item that cannot be read as an option.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.runner import RunResult
from agent.services.wolfbet import WolfBetPaths, WolfBetService, config_pair
from agent.validators import InvalidArgs

SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "wolfbet-config-schema.json").read_text())
PATHS = WolfBetPaths(
    python=Path("/opt/wb/venv/bin/python3"),
    bot=Path("/opt/wb/wolfbet.py"),
    ctl=Path("/opt/bin/wolfctl"),
    repo=Path("/opt/repo"),
    export_dir=Path("/var/agent/exports"),
    journalctl=Path("/usr/bin/journalctl"),
)
FIXED = {str(PATHS.python), str(PATHS.bot), str(PATHS.ctl), str(PATHS.journalctl)}
HOSTILE = [
    "; rm -rf /",
    "$(id)",
    "`id`",
    "a|b",
    "a&&b",
    "a\nb",
    "a\x00b",
    "--json",
    "-rf",
    "--branch=x",
    "../../etc/passwd",
    "/etc/passwd",
    "a b",
    "'",
    '"',
    "\\",
    "*",
    "~",
    "$HOME",
    "a\rb",
    "é",
    "x" * 300,
    "",
    " ",
    "--",
    "-",
    "2026-11-01;id",
    "2026-11-01\n--json",
    "all;id",
]
VALUES = st.one_of(
    st.sampled_from(HOSTILE),
    st.text(max_size=80),
    st.text(alphabet="-;|&$`\n\x00 ./\\abc=:", max_size=30),
)
STRING_ARGS = [
    ("stats", "since"),
    ("stats", "until"),
    ("analytics", "since"),
    ("analytics", "until"),
    ("preset", "name"),
    ("backtest", "preset"),
    ("update", "branch"),
    ("pause", "target"),
    ("resume", "target"),
    ("reload", "target"),
    ("lanes", "split"),
    ("logs", "service"),
    ("logs_follow", "service"),
    ("export", "format"),
]
BASE = {"lanes": {"count": 2}}


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(
        self, argv: list[str], timeout_s: float, cwd: Path | None = None
    ) -> RunResult:
        self.calls.append(list(argv))
        return RunResult(0, json.dumps(SCHEMA) if argv[-1] == "--config-schema" else "{}", "")


def _check_argv(argv: list[str], value: str) -> None:
    for item in argv:
        assert "\x00" not in item and "\n" not in item and "\r" not in item
    user_items = [item for item in argv if item not in FIXED and not item.startswith("--")]
    assert not any(item.startswith("-") for item in user_items), argv
    assert sum(1 for item in argv if value.strip() and value.strip() in item.split("=")[-1:]) <= 1


@settings(max_examples=60, deadline=None)
@given(case=st.sampled_from(STRING_ARGS), value=VALUES)
def test_string_args_are_refused_or_one_inert_item(case: tuple[str, str], value: str) -> None:
    name, field = case
    recorder = Recorder()
    args: dict[str, Any] = {**BASE.get(name, {}), field: value}
    try:
        asyncio.run(WolfBetService(PATHS, recorder).run_command(name, args))
    except InvalidArgs:
        assert recorder.calls == []
        return
    assert len(recorder.calls) == 1
    _check_argv(recorder.calls[0], value)


@settings(max_examples=200, deadline=None)
@given(
    key=st.one_of(
        st.sampled_from(
            [
                "maxloss",
                "proxy",
                "events_url",
                "lane2.maxloss",
                "lanes",
                "game",
                "sdloss",
                "recurring",
            ]
        ),
        VALUES,
    ),
    value=VALUES,
)
def test_config_pairs_are_refused_or_one_safe_item(key: str, value: str) -> None:
    try:
        item = config_pair(SCHEMA, key, value)
    except InvalidArgs:
        return
    name, _, canonical = item.partition("=")
    assert name.replace(".", "").replace("_", "").isalnum() and not item.startswith("-")
    assert not any(ch.isspace() or ord(ch) < 32 for ch in canonical)
    assert canonical and "\x00" not in item


@settings(max_examples=60, deadline=None)
@given(pairs=st.dictionaries(VALUES, VALUES, min_size=1, max_size=4))
def test_config_set_with_hostile_pairs_never_reaches_the_set(pairs: dict[str, str]) -> None:
    recorder = Recorder()
    try:
        asyncio.run(WolfBetService(PATHS, recorder).config_set({"pairs": pairs}))
    except InvalidArgs:
        assert all(call[-1] == "--config-schema" for call in recorder.calls)
        return
    for call in recorder.calls:
        for item in call:
            assert "\x00" not in item and "\n" not in item
