"""Deterministic in-process OpenAPI export used by the frontend contract gate."""

import json
import os
import subprocess
import sys
from pathlib import Path

from src.openapi_export import export_schema


def test_openapi_export_is_deterministic_and_describes_http_only(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "nested" / "second.json"

    export_schema(first)
    export_schema(second)

    content = first.read_text(encoding="utf-8")
    assert content == second.read_text(encoding="utf-8")
    assert content.endswith("\n")

    payload = json.loads(content)
    assert payload["info"]["title"]
    assert "/api/me" in payload["paths"]
    assert "/api/raw-materials/movement" in payload["paths"]
    assert not any("collab" in path for path in payload["paths"])
    movement = payload["components"]["schemas"]["MovementRowRead"]["properties"]
    assert {"id", "assetId", "businessPlanTons", "createdAt"} <= set(movement)


def test_openapi_export_needs_no_database_credentials(tmp_path):
    """CI's freshness gate has no .env and no PostgreSQL/AUTH settings."""
    back_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith(("POSTGRES_", "AUTH_", "DATABASE_URL", "KEYCLOAK_")):
            env.pop(name)
    env["PYTHONPATH"] = str(back_dir)
    output = tmp_path / "openapi.json"

    # A clean cwd means Settings cannot find a dotenv file either.
    result = subprocess.run(  # noqa: S603 - fixed module invocation of this interpreter
        [sys.executable, "-m", "src.openapi_export", str(output)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["info"]["title"]
    assert "/api/me" in payload["paths"]
