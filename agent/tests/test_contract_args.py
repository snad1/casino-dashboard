"""The argument schemas the dashboard's control API mirrors stay pinned in `contracts/`.

Run with `CONTRACT_WRITE=1` after changing one of these argument models to rewrite the
file; the api's own test then fails until its body models match again.
"""

import json
import os
from pathlib import Path

from agent.services import goated
from agent.services.wolfbet import COMMANDS, ConfigArgs

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts/schemas"
CONTRACT = SCHEMAS / "agent-wolfbet-args-v1.json"
GOATED_CONTRACT = SCHEMAS / "agent-goated-args-v1.json"
CONTROL_COMMANDS = (
    "start",
    "stop",
    "lane_stop",
    "lane_start",
    "lane_restart",
    "lane_restart_end",
    "lane_add",
    "lane_remove",
    "lane_rename",
    "pause",
    "resume",
    "reload",
    "restart",
    "restart_rolling",
    "lanes",
    "reset",
    "run_reset",
    "stop_all",
    "cleanup",
    "update",
    "doctor",
    "logs",
    "logs_follow",
    "diagnose",
    "benchmark",
    "backtest",
    "export",
    "archives",
    "start_all",
    "status_all",
    "version",
    *(f"tg.{v}" for v in ("start", "stop", "restart", "status")),
)


def test_control_argument_schemas_match_the_contract() -> None:
    schemas = {name: COMMANDS[name].args.model_json_schema() for name in CONTROL_COMMANDS}
    schemas["config_set"] = ConfigArgs.model_json_schema()
    document = json.dumps(schemas, indent=2, sort_keys=True) + "\n"
    if os.environ.get("CONTRACT_WRITE") == "1":
        CONTRACT.write_text(document)
    assert CONTRACT.exists(), "the args contract is missing; run this test with CONTRACT_WRITE=1"
    assert CONTRACT.read_text() == document, (
        "the args contract is stale; rerun with CONTRACT_WRITE=1"
    )


def test_goateds_control_argument_schemas_match_its_contract() -> None:
    names = [name for name in CONTROL_COMMANDS if name in goated.COMMANDS]
    schemas = {name: goated.COMMANDS[name].args.model_json_schema() for name in names}
    schemas["config_set"] = ConfigArgs.model_json_schema()
    document = json.dumps(schemas, indent=2, sort_keys=True) + "\n"
    if os.environ.get("CONTRACT_WRITE") == "1":
        GOATED_CONTRACT.write_text(document)
    assert GOATED_CONTRACT.read_text() == document, (
        "the goated args contract is stale; rerun with CONTRACT_WRITE=1"
    )
