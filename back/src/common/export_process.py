"""Isolate Python-heavy workbook generation from the ASGI interpreter."""

import asyncio
import json
import logging
import sys

from src.app_logging import log_error
from src.common.export_jobs import ExportJob, ExportSpec, final_path


class ExportProcessError(RuntimeError):
    """A worker exited without publishing a complete workbook."""


class RetryableExportProcessError(ConnectionError):
    """The isolated worker lost a transient dependency."""


async def generate_in_process(job: ExportJob) -> None:
    assert isinstance(job.spec, ExportSpec)  # noqa: S101
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.scripts.export_worker",
        job.id,
        job.file_id,
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None  # noqa: S101
    retryable = False
    try:
        async for line in process.stdout:
            event = json.loads(line)
            if event.get("event") == "progress":
                job.progress = int(event["progress"])
                job.spec.total = int(event["total"])
            elif event.get("event") == "failed":
                retryable = bool(event.get("retryable"))
                log_error(
                    logging.getLogger("reportcollector.application"),
                    "export.process_failed",
                    "Ошибка процесса формирования выгрузки.",
                    job_id=job.id,
                    error=event.get("error"),
                )
        code = await process.wait()
        if code:
            message = f"Export worker exited with code {code}"
            error_type = RetryableExportProcessError if retryable else ExportProcessError
            raise error_type(message)
        job.path = final_path(job.file_id)
        job.size = job.path.stat().st_size
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
