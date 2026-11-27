"""`<bot>.update_check`: how far a bot's checkout is behind its upstream, from git alone."""

from pathlib import Path

import pytest

from agent.runner import CommandTimeout, RunResult
from agent.services.common import BotPaths, BotService

REPO = Path("/root/demo")
GIT = Path("/usr/bin/git")
PATHS = BotPaths(
    python=Path("/root/demo-bot/venv/bin/python3"),
    bot=Path("/root/demo-bot/demo.py"),
    ctl=Path("/root/.local/bin/democtl"),
    repo=REPO,
    export_dir=Path("/var/lib/casino-agent/exports"),
    journalctl=Path("/usr/bin/journalctl"),
    git=GIT,
)
PREFIX = [str(GIT), "-C", str(REPO)]


class Git:
    """Answers the three git calls in order; records what ran."""

    def __init__(self, *replies: RunResult | Exception) -> None:
        self.replies = list(replies)
        self.argvs: list[list[str]] = []

    async def __call__(self, argv: list[str], timeout_s: float, **_: object) -> RunResult:
        self.argvs.append(argv)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _ok(stdout: str = "") -> RunResult:
    return RunResult(0, stdout, "")


def _service(git: Git) -> BotService:
    return BotService(PATHS, runner=git, name="demo")


async def test_counts_commits_behind_and_both_hashes() -> None:
    git = Git(_ok(), _ok("3\n"), _ok("abc1234\ndef5678\n"))
    data = await _service(git).handlers()["demo.update_check"]({})
    assert data == {"behind": 3, "head": "abc1234", "latest": "def5678"}
    assert git.argvs == [
        [*PREFIX, "fetch", "--quiet"],
        [*PREFIX, "rev-list", "--count", "HEAD..@{upstream}"],
        [*PREFIX, "rev-parse", "--short", "HEAD", "@{upstream}"],
    ]


UNKNOWN = {"behind": None, "head": None, "latest": None}


@pytest.mark.parametrize(
    "replies",
    [
        (RunResult(128, "", "fatal: could not read from remote /root/secret"),),
        (_ok(), RunResult(128, "", "fatal: no upstream configured"), _ok("abc1234\n")),
        (_ok(), _ok("lots\n"), _ok("abc1234\ndef5678\n")),
        (_ok(), _ok("1\n"), _ok("abc1234\n")),
        (_ok(), _ok("1\n"), _ok("abc1234\n<script>\n")),
        (CommandTimeout("git fetch"),),
    ],
)
async def test_a_git_failure_says_nothing_and_leaks_nothing(
    replies: tuple[RunResult | Exception, ...],
) -> None:
    data = await _service(Git(*replies)).update_check({})
    assert data == UNKNOWN


def test_a_relative_git_path_names_its_variable(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "CASINO_AGENT_GIT": "git"}
    with pytest.raises(ValueError, match="CASINO_AGENT_GIT"):
        BotPaths.from_env(
            "demo", env, bot_file="d.py", install_dir="d", repo_dir="d", ctl_name="democtl"
        )


def test_git_defaults_to_the_system_binary(tmp_path: Path) -> None:
    paths = BotPaths.from_env(
        "demo",
        {"HOME": str(tmp_path)},
        bot_file="d.py",
        install_dir="d",
        repo_dir="d",
        ctl_name="c",
    )
    assert paths.git == GIT
