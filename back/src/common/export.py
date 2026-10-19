"""
Excel-export plumbing shared by every project's forms.

It covers request validation shared by every project («Сырьё», «Ремонт»,
КППК): limits, uniqueness, header shape, file name, a per-section allowlist.
A project module keeps only its vocabulary — sheet names, allowed keys, which
columns reference which list — and calls into here (see
``src/renovation/export.py``, ``src/raw_materials/export.py``,
``src/kppk/export.py``).

The workbook RENDERING itself is streaming raw-XML via
``src.common.xlsx_stream`` / ``src.common.xlsx_table``: every export — a
20-row dictionary or a 300k-row plan — runs through one bounded-memory
pipeline whose bytes can be downloaded while the file is still generated.

User-facing strings (error messages) are Russian by the project standard.
"""

from collections.abc import Sequence
from typing import Protocol

MAX_EXPORT_ROWS = 100_000
MAX_EXPORT_COLUMNS = 64
MAX_CELL_CHARS = 32_767

# The row count at which a request switches from "render in the request"
# (200 + bytes) to a background job (202 + polling). Covers the memory of a
# fully materialized row list and the openpyxl-free render time; anything
# above goes through the same bounded-memory worker the plan export uses.
INLINE_EXPORT_MAX_ROWS = 5_000

NUMBER_FORMAT_DEFAULT = "General"
DATETIME_FORMAT = "DD.MM.YYYY HH:MM:SS"
LISTS_SHEET_NAME = "Списки"


class ExportValidationError(ValueError):
    """Invalid Excel export request (surfaced as HTTP 400)."""


class ExportTooLargeError(RuntimeError):
    """
    The sheet part outgrew the plain-ZIP entry ceiling (4 GiB uncompressed).

    The append-only container deliberately stays off zip64: a streamed zip64
    entry's zeroed local-header extra is rejected by real Excel. A sheet past
    the ceiling therefore cannot be streamed at all — the write fails cleanly
    instead of producing a file Excel would repair.
    """


# Shared Russian error texts — one wording for every project.
ERROR_COLUMN_COUNT = "Некорректное количество экспортируемых столбцов."
ERROR_TOO_MANY_ROWS = "Слишком много строк для экспорта."
ERROR_DUPLICATE_ROWS = "Список экспортируемых строк содержит дубли."
ERROR_BAD_HEADERS = "Некорректное описание заголовков экспорта."
ERROR_HIDDEN_COLUMN = "Экспорт содержит скрытый или недоступный столбец."
ERROR_BAD_FILENAME = "Имя файла должно заканчиваться на .xlsx и не содержать путей."
ERROR_EXPORT_TOO_LARGE = (
    "Выгрузка превышает 4 ГБ — Excel не сможет открыть такой файл. "
    "Сузьте область выгрузки (блок, предприятие или набор строк)."
)


class ExportColumnLike(Protocol):
    """
    The column descriptor the shared export code relies on.

    Read-only members: the pydantic request models stay assignable (a writable
    protocol member would demand their list fields to accept ANY Sequence).
    """

    @property
    def key(self) -> str: ...  # camelCase is the frontend contract

    @property
    def headerPath(self) -> list[str]: ...  # noqa: N802  # camelCase is the frontend contract

    @property
    def width(self) -> float: ...

    @property
    def numberFormat(self) -> str | None: ...  # noqa: N802  # camelCase is the frontend contract


class ExportRequestLike(Protocol):
    """The part of a project's export request the shared validation reads."""

    @property
    def columns(self) -> Sequence[ExportColumnLike]: ...

    @property
    def rowIds(self) -> Sequence[object] | None: ...  # noqa: N802  # camelCase is the frontend contract

    @property
    def fileName(self) -> str: ...  # noqa: N802  # camelCase is the frontend contract


def validate_export_shape(request: ExportRequestLike) -> None:
    """Validate the project-independent part of a request: limits, uniqueness, header shape, file name."""
    if not request.columns or len(request.columns) > MAX_EXPORT_COLUMNS:
        raise ExportValidationError(ERROR_COLUMN_COUNT)
    if request.rowIds is not None:
        if len(request.rowIds) > MAX_EXPORT_ROWS:
            raise ExportValidationError(ERROR_TOO_MANY_ROWS)
        if len(set(request.rowIds)) != len(request.rowIds):
            raise ExportValidationError(ERROR_DUPLICATE_ROWS)
    seen: set[str] = set()
    for column in request.columns:
        if (
            not column.key.strip()
            or not column.headerPath
            or any(not part.strip() for part in column.headerPath)
            or column.key in seen
        ):
            raise ExportValidationError(ERROR_BAD_HEADERS)
        seen.add(column.key)
    filename = request.fileName.strip()
    if not filename.lower().endswith(".xlsx") or any(char in filename for char in "/\\"):
        raise ExportValidationError(ERROR_BAD_FILENAME)


def validate_columns_allowed(columns: Sequence[ExportColumnLike], allowed: frozenset[str]) -> None:
    """Reject columns outside a section's allowlist (hidden or unavailable)."""
    if any(column.key not in allowed for column in columns):
        raise ExportValidationError(ERROR_HIDDEN_COLUMN)


def safe_sheet_name(value: str) -> str:
    """Return a valid worksheet title, «Отчёт» for names Excel cannot carry."""
    trimmed = value.strip()
    if not trimmed or len(trimmed) > 31 or any(char in trimmed for char in "[]:*?/\\"):
        return "Отчёт"
    return trimmed


def truncate(value: str) -> str:
    """Clamp a cell string to Excel's character limit."""
    if len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS]
    return value
