"""Supervise a cancellable import in its own Python interpreter."""

import asyncio
import json
import sys
from pathlib import Path

from src.common.durable_imports import ImportJob, ImportValidationError


async def generate_import_in_process(job: ImportJob, _source: Path) -> dict:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.scripts.import_worker",
        job.id,
        str(job.lease_token),
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None  # noqa: S101
    retryable = False
    detail = None
    result: dict = {}
    try:
        async for line in process.stdout:
            event = json.loads(line)
            if event.get("event") == "progress":
                job.progress = int(event["progress"])
                job.total = int(event.get("total", 0))
                phase = event.get("phase")
                if phase:
                    job.phase = str(phase)
            elif event.get("event") == "done":
                result = event["result"]
            elif event.get("event") == "failed":
                retryable = bool(event.get("retryable"))
                detail = event.get("detail")
        code = await process.wait()
        if code:
            if retryable:
                msg = "Import dependency unavailable"
                raise ConnectionError(msg)
            if detail:
                raise ImportValidationError(str(detail))
            msg = f"Import worker exited with code {code}"
            raise RuntimeError(msg)
        return result
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
