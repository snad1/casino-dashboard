"""The committed OpenAPI file matches the api, so the frontend's generated types stay true.

Run `make openapi` after changing a route or a model to rewrite the file and the types.
"""

import json
import os
from pathlib import Path

from app.main import create_app
from app.settings import Settings

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def test_openapi_file_matches_the_api(settings: Settings) -> None:
    schema = json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("OPENAPI_WRITE") == "1":
        CONTRACT.write_text(schema)
    assert CONTRACT.exists(), "contracts/openapi.json is missing; run `make openapi`"
    assert CONTRACT.read_text() == schema, "contracts/openapi.json is stale; run `make openapi`"
