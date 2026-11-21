"""The request envelope is closed: anything off-spec is rejected before dispatch."""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from agent.protocol import PROTO_VERSION, Request, canonical_body, sign, verify_mac

TOKEN = b"k" * 16 + b"0123456789abcdefXYZ"
SIGNED = {"ts": 1_700_000_000_000, "mac": "0" * 64}


def test_valid_request_parses() -> None:
    req = Request.model_validate({"proto": PROTO_VERSION, "id": "r1", "cmd": "status", **SIGNED})
    assert req.cmd == "status"
    assert req.args == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"proto": 2, "id": "r1", "cmd": "status", **SIGNED},
        {"proto": 1, "id": "r1", "cmd": "status", "shell": "rm -rf /", **SIGNED},
        {"proto": 1, "id": "r1", "cmd": 5, **SIGNED},
        {"proto": "1", "id": "r1", "cmd": "status", **SIGNED},
        {"proto": 1, "id": "", "cmd": "status", **SIGNED},
        {"proto": 1, "id": "r1", "cmd": "status; reboot", **SIGNED},
        {"proto": 1, "id": "r1", "cmd": "status"},
        {"proto": 1, "id": "r1", "cmd": "status", "ts": "1", "mac": "0" * 64},
        {"proto": 1, "id": "r1", "cmd": "status", "ts": -1, "mac": "0" * 64},
        {"proto": 1, "id": "r1", "cmd": "status", "ts": 1, "mac": "Z" * 64},
        {"proto": 1, "id": "r1", "cmd": "status", "ts": 1, "mac": "0" * 63},
    ],
)
def test_off_spec_requests_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Request.model_validate(payload)


@given(st.text())
def test_cmd_never_accepts_shell_metacharacters(cmd: str) -> None:
    try:
        req = Request.model_validate({"proto": 1, "id": "r1", "cmd": cmd, **SIGNED})
    except ValidationError:
        return
    assert not set(req.cmd) & set(";|&$`<> \n\t'\"")


def test_canonical_body_ignores_key_order_and_the_mac() -> None:
    a = {"proto": 1, "id": "r1", "cmd": "ping", "args": {"b": 1, "a": 2}, "ts": 5}
    b = {"ts": 5, "args": {"a": 2, "b": 1}, "cmd": "ping", "id": "r1", "proto": 1, "mac": "x"}
    assert canonical_body(a) == canonical_body(b)
    assert canonical_body(a) == b'{"args":{"a":2,"b":1},"cmd":"ping","id":"r1","proto":1,"ts":5}'


@given(st.dictionaries(st.text(max_size=5), st.integers(), max_size=3))
def test_any_change_to_a_signed_field_breaks_the_mac(args: dict[str, int]) -> None:
    fields = {"proto": 1, "id": "r1", "cmd": "ping", "args": args, "ts": 5}
    req = Request.model_validate({**fields, "mac": sign(TOKEN, fields)})
    assert verify_mac(TOKEN, req)
    for changed in (
        {**fields, "id": "r2"},
        {**fields, "ts": 6},
        {**fields, "cmd": "pong"},
        {**fields, "args": {**args, "extra": 1}},
    ):
        assert not verify_mac(TOKEN, Request.model_validate({**changed, "mac": req.mac}))
    assert not verify_mac(b"another-token-0123456789abcdefghij", req)


# ── contract copy ──
def _fixture(name: str) -> bytes:
    import pathlib

    return (pathlib.Path(__file__).parent / "fixtures" / name).read_bytes()


def test_schema_copy_matches_its_checksum() -> None:
    import hashlib

    digest, name = _fixture("SHA256SUMS").decode().split()
    assert hashlib.sha256(_fixture(name)).hexdigest() == digest


def test_schema_fields_match_the_models() -> None:
    import json

    from agent.protocol import Response

    defs = json.loads(_fixture("agent-proto-v1.json"))["$defs"]
    req, resp = Request.model_json_schema(), Response.model_json_schema()
    assert set(defs["request"]["properties"]) == set(req["properties"])
    assert set(defs["request"]["required"]) == set(req["required"])
    assert set(defs["response"]["properties"]) == set(resp["properties"])
    codes = defs["response"]["properties"]["error"]["properties"]["code"]["enum"]
    assert codes == list(resp["$defs"]["Error"]["properties"]["code"]["enum"])
    from agent.protocol import Frame

    assert set(defs["frame"]["properties"]) == set(Frame.model_json_schema()["properties"])
    from agent.protocol import Chunk

    assert set(defs["chunk"]["properties"]) == set(Chunk.model_json_schema()["properties"])


# ── golden wire fixtures shared with the bots ──
_GOLDEN = Path(__file__).resolve().parents[2] / "contracts/fixtures/agent_proto_v1"


def test_golden_fixtures_match_their_checksums() -> None:
    import hashlib

    for line in (_GOLDEN / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        assert hashlib.sha256((_GOLDEN / name).read_bytes()).hexdigest() == digest, name


def test_golden_request_mac_and_replies_are_what_the_agent_produces() -> None:
    import json

    from agent.protocol import Response

    fixture = json.loads((_GOLDEN / "request.json").read_text())
    request = Request.model_validate(fixture["request"])
    assert verify_mac(fixture["token"].encode(), request)
    assert sign(fixture["token"].encode(), fixture["request"]) == fixture["request"]["mac"]
    for line in (_GOLDEN / "responses.jsonl").read_bytes().splitlines(keepends=True):
        assert Response.model_validate_json(line).line() == line
