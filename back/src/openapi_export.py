"""
Deterministic, in-process export of the live FastAPI OpenAPI contract.

Used by the frontend type generator and the CI freshness gate. No HTTP server,
database or network access is needed: only the app's route declarations.
"""

import argparse
import json
from pathlib import Path

from src.main import app


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
