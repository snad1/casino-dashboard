"""The request envelope is closed: anything off-spec is rejected before dispatch."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from agent.protocol import PROTO_VERSION, Request


def test_valid_request_parses() -> None:
    req = Request.model_validate({"proto": PROTO_VERSION, "id": "r1", "cmd": "status"})
    assert req.cmd == "status"
    assert req.args == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"proto": 2, "id": "r1", "cmd": "status"},
        {"proto": 1, "id": "r1", "cmd": "status", "shell": "rm -rf /"},
        {"proto": 1, "id": "r1", "cmd": 5},
        {"proto": "1", "id": "r1", "cmd": "status"},
        {"proto": 1, "id": "", "cmd": "status"},
        {"proto": 1, "id": "r1", "cmd": "status; reboot"},
    ],
)
def test_off_spec_requests_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Request.model_validate(payload)


@given(st.text())
def test_cmd_never_accepts_shell_metacharacters(cmd: str) -> None:
    try:
        req = Request.model_validate({"proto": 1, "id": "r1", "cmd": cmd})
    except ValidationError:
        return
    assert not set(req.cmd) & set(";|&$`<> \n\t'\"")
