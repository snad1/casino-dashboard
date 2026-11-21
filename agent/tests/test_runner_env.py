import os
from pathlib import Path

from agent.runner import scrubbed_env


def test_a_bare_system_unit_environment_still_gets_home_and_the_user_runtime_dir() -> None:
    env = scrubbed_env({"USER": "root"})
    assert env == {"HOME": str(Path.home()), "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}


def test_values_the_agent_already_has_are_kept_and_others_are_dropped() -> None:
    env = scrubbed_env(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/bot",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "CASINO_AGENT_TOKEN": "secret",
        }
    )
    assert env == {"PATH": "/usr/bin", "HOME": "/home/bot", "XDG_RUNTIME_DIR": "/run/user/1000"}
