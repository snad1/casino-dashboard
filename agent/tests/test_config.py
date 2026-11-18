"""The agent refuses to start with a configuration that would weaken the socket."""

from pathlib import Path

import pytest

from agent.config import ConfigError, load_config, weak_token_reason

GOOD = "Kq9vX2mLp7RtZ4wYb8NcFj3HdGs6Ae1U-good-token"


def _env(tmp_path: Path, token: str = GOOD, mode: int = 0o600, **over: str) -> dict[str, str]:
    token_file = tmp_path / "token"
    token_file.write_text(token + "\n")
    token_file.chmod(mode)
    env = {
        "CASINO_AGENT_SOCKET": str(tmp_path / "a.sock"),
        "CASINO_AGENT_TOKEN_FILE": str(token_file),
        "CASINO_AGENT_ALLOW_UIDS": "10001, 10002",
    }
    env.update(over)
    return {k: v for k, v in env.items() if v != "<unset>"}


def test_good_config_loads(tmp_path: Path) -> None:
    cfg = load_config(_env(tmp_path, CASINO_AGENT_GROUP="casino-agent"))
    assert cfg.token == GOOD.encode()
    assert cfg.allow_uids == {10001, 10002} and cfg.allow_gids == frozenset()
    assert cfg.group == "casino-agent"
    assert (cfg.max_request_bytes, cfg.idle_timeout, cfg.replay_window_ms) == (65536, 5.0, 30000)
    assert GOOD not in repr(cfg)


def test_socket_defaults_to_the_systemd_runtime_directory(tmp_path: Path) -> None:
    env = _env(
        tmp_path, CASINO_AGENT_SOCKET="<unset>", RUNTIME_DIRECTORY="/run/user/0/casino-agent"
    )
    assert str(load_config(env).socket_path) == "/run/user/0/casino-agent/agent.sock"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "changeme",
        "x" * 40,
        "aaaaaaaabbbbbbbbccccccccdddddddd",
        "short-but-varied-1234",
        "change-me-please-0123456789abcdefgh",
        "your-token-here-0123456789abcdefgh",
    ],
)
def test_weak_tokens_are_refused_without_echoing_them(tmp_path: Path, token: str) -> None:
    with pytest.raises(ConfigError) as e:
        load_config(_env(tmp_path, token=token))
    if token:
        assert token not in str(e.value)


@pytest.mark.parametrize("mode", [0o640, 0o644, 0o604])
def test_token_file_readable_by_others_is_refused(tmp_path: Path, mode: int) -> None:
    with pytest.raises(ConfigError, match="owner only"):
        load_config(_env(tmp_path, mode=mode))


def test_missing_token_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot be read"):
        load_config(_env(tmp_path, CASINO_AGENT_TOKEN_FILE=str(tmp_path / "nope")))


@pytest.mark.parametrize(
    "over,message",
    [
        ({"CASINO_AGENT_ALLOW_UIDS": ""}, "empty allowlist"),
        ({"CASINO_AGENT_ALLOW_UIDS": "root"}, "numeric ids"),
        ({"CASINO_AGENT_SOCKET": "<unset>"}, "CASINO_AGENT_SOCKET"),
        ({"CASINO_AGENT_TOKEN_FILE": "<unset>"}, "CASINO_AGENT_TOKEN_FILE"),
    ],
)
def test_incomplete_config_is_refused(tmp_path: Path, over: dict[str, str], message: str) -> None:
    env = {**_env(tmp_path), **over}
    with pytest.raises(ConfigError, match=message):
        load_config({k: v for k, v in env.items() if v != "<unset>"})


def test_gid_only_allowlist_is_enough(tmp_path: Path) -> None:
    cfg = load_config(_env(tmp_path, CASINO_AGENT_ALLOW_UIDS="", CASINO_AGENT_ALLOW_GIDS="997"))
    assert cfg.allow_gids == {997}


def test_weak_token_reason_accepts_a_generated_token() -> None:
    import secrets

    assert weak_token_reason(secrets.token_urlsafe(48)) is None
