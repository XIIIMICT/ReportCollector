"""Bound import request bodies before Starlette spools multipart files to disk."""

import asyncio
from time import monotonic

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.metrics import IMPORT_REJECTIONS

IMPORT_PATHS = frozenset({"/api/renovation/plan/import/inspect", "/api/renovation/plan/import/apply"})
# The route enforces the exact file limit; the wire body also contains headers,
# boundaries and form fields. Bound that overhead separately.
MULTIPART_ALLOWANCE_BYTES = 1024 * 1024


class ImportUploadLimitsMiddleware:
    """Reject oversized or slow import bodies, including chunked transfers."""

    def __init__(self, app: ASGIApp, *, max_file_bytes: int, timeout_seconds: float) -> None:
        self.app = app
        self.max_body_bytes = max_file_bytes + MULTIPART_ALLOWANCE_BYTES
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") not in IMPORT_PATHS:
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length" and value.isdigit() and int(value) > self.max_body_bytes:
                IMPORT_REJECTIONS.labels(reason="body_size").inc()
                await JSONResponse({"detail": "Файл имеет слишком большой размер."}, status_code=413)(
                    scope, receive, send
                )
                return

        deadline = monotonic() + self.timeout_seconds
        consumed = 0
        complete = False

        async def limited_receive() -> Message:
            nonlocal consumed, complete
            if complete:
                return await receive()
            try:
                async with asyncio.timeout(max(0, deadline - monotonic())):
                    message = await receive()
            except TimeoutError as error:
                IMPORT_REJECTIONS.labels(reason="upload_timeout").inc()
                raise HTTPException(408, "Превышено время загрузки файла. Попробуйте ещё раз.") from error
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_body_bytes:
                    IMPORT_REJECTIONS.labels(reason="body_size").inc()
                    # Starlette closes partial multipart files on stream errors;
                    # FastAPI preserves this status instead of a generic 400.
                    raise HTTPException(413, "Файл имеет слишком большой размер.")
                complete = not message.get("more_body", False)
            return message

        await self.app(scope, limited_receive, send)
