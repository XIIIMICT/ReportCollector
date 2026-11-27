"""Deterministic in-process OpenAPI export used by the frontend contract gate."""

import json

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
