"""
Deterministic, in-process export of the live FastAPI OpenAPI contract.

Used by the frontend type generator and the CI freshness gate. No HTTP server,
database or network access is needed: only the app's route declarations.

Importing `src.main` constructs `Settings`, which normally requires the real
PostgreSQL settings and a 32-character AUTH_SECRET. The CI freshness job has
none of them (and must not need credentials), so this entrypoint supplies
clearly synthetic placeholders BEFORE importing the app. They are never used
to connect anywhere — the export only reads route and schema declarations.
Real values, when present, are never overwritten.
"""

import argparse
import importlib
import json
import os
from pathlib import Path

from fastapi import FastAPI

# Synthetic values only. Do not reuse them as real credentials or host names.
_EXPORT_PLACEHOLDER_SETTINGS = {
    "POSTGRES_USER": "openapi_export_placeholder",
    "POSTGRES_PASSWORD": "openapi_export_placeholder",
    "POSTGRES_DB": "openapi_export_placeholder",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "POSTGRES_SCHEMA": "public",
    "AUTH_SECRET": "openapi-export-placeholder-not-a-real-secret",
}


def _load_app() -> FastAPI:
    """Import the live app with placeholder settings for a schema-only export."""
    for name, value in _EXPORT_PLACEHOLDER_SETTINGS.items():
        if not os.environ.get(name):
            os.environ[name] = value
    return importlib.import_module("src.main").app


app = _load_app()


def export_schema(path: Path) -> None:
    """Write a canonical JSON representation of `app.openapi()`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the ReportCollector OpenAPI schema.")
    parser.add_argument("output", type=Path, help="Destination JSON file (UTF-8, LF, sorted keys).")
    export_schema(parser.parse_args().output)


if __name__ == "__main__":
    main()
