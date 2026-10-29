"""Exercise early rejection through the real multipart parser and HTTP stack."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from fastapi import FastAPI, File, UploadFile
from httpx2 import ASGITransport, AsyncClient

from src.common.upload_limits import MULTIPART_ALLOWANCE_BYTES, ImportUploadLimitsMiddleware

PATH = "/api/renovation/plan/import/inspect"


def upload_app(*, timeout: float = 1) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ImportUploadLimitsMiddleware, max_file_bytes=8, timeout_seconds=timeout)

    @app.post(PATH)
    async def inspect(file: Annotated[UploadFile, File()]) -> dict[str, int]:
        # Application processing time must not be part of the upload timeout.
        await asyncio.sleep(0.03)
        return {"size": len(await file.read())}

    return app


async def test_oversized_content_length_is_rejected_without_consuming_body() -> None:
    async def body() -> AsyncIterator[bytes]:
        pytest.fail("a declared oversized body should not be read")
        yield b""

    async with AsyncClient(transport=ASGITransport(app=upload_app()), base_url="http://test") as client:
        response = await client.post(
            PATH,
            headers={"Content-Length": str(MULTIPART_ALLOWANCE_BYTES + 9)},
            content=body(),
        )
    assert response.status_code == 413


@pytest.mark.parametrize("slow", [False, True])
async def test_streamed_multipart_limits_are_preserved_as_http_errors(slow: bool) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield (
            b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="plan.xlsb"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\ninitial"
        )
        if slow:
            await asyncio.sleep(1)
        yield b"x" * (MULTIPART_ALLOWANCE_BYTES + 9)
        yield b"\r\n--test-boundary--\r\n"

    async with AsyncClient(transport=ASGITransport(app=upload_app(timeout=0.02)), base_url="http://test") as client:
        response = await client.post(
            PATH, headers={"Content-Type": "multipart/form-data; boundary=test-boundary"}, content=body()
        )
    assert response.status_code == (408 if slow else 413)


async def test_completed_upload_does_not_time_out_during_processing() -> None:
    async with AsyncClient(transport=ASGITransport(app=upload_app(timeout=0.02)), base_url="http://test") as client:
        response = await client.post(PATH, files={"file": ("plan.xlsb", b"sample")})
    assert response.status_code == 200
    assert response.json() == {"size": 6}
