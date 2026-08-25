"""HTTP file-download helpers (Content-Disposition with non-ASCII filenames)."""

from urllib.parse import quote

from fastapi.responses import Response

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def attachment_response(content: bytes, *, filename: str, media_type: str = XLSX_MEDIA_TYPE) -> Response:
    """
    Build a download response whose filename may contain any Unicode characters.

    HTTP headers are latin-1, so the filename is percent-encoded; the RFC 5987
    ``filename*`` parameter carries the real name and the ASCII-safe quoted
    ``filename`` is the fallback for old clients.
    """
    quoted = quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )
