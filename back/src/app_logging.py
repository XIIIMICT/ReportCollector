import json
import logging
import re
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# Rotation budget: 64 MB per file, 5 files — at most ~320 MB on disk.
# The dev tuner reached 174 MB in 33 hours, so one file holds roughly half a
# day at that rate; a week of history is not the goal (Loki holds that).
LOG_MAX_BYTES = 64 * 1024 * 1024
LOG_BACKUP_COUNT = 5


class CredentialQueryFilter(logging.Filter):
    """Keep one-time WebSocket tickets out of ASGI server access logs."""

    pattern = re.compile(r"([?&](?:ticket|access_token|token)=)[^&\s\"']+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.pattern.sub(r"\1[REDACTED]", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                self.pattern.sub(r"\1[REDACTED]", value) if isinstance(value, str) else value for value in record.args
            )
        return True


# Only bounded correlation and classification fields reach the shared log
# pipeline. Identity claims, raw request values and exception text are excluded:
# Loki is operational infrastructure, not a store for personal data. Any
# `exc_info` attached by a caller is reduced to a stable error_type/error_code —
# never formatted as a traceback — while request/job ids, phase and other
# allowlisted fields survive. Failure diagnostics therefore use error_code /
# error_type, never a free-form `error` value or a nested cause.
SAFE_LOG_FIELDS = (
    "event",
    "request_id",
    "route",
    "request_bytes",
    "response_bytes",
    "operation",
    "phase",
    "outcome",
    "job_id",
    "attempt",
    "error_code",
    "queue_wait_ms",
    "rows",
    "bytes",
    "duration_ms",
    "error_type",
    "form_id",
    "form_db_name",
    "form_name",
    "form_row_mode",
    "field_id",
    "field_db_name",
    "field_count",
    "rows_affected",
    "rows_deleted",
    "loaded_for",
    "period",
    "access_rule_id",
    "access_mode",
    "user_ids_count",
    "field_ids_count",
    "filter_count",
    "method",
    "status_code",
    "template_row_id",
    "record_id",
    "catalog_db_name",
    "project_key",
    "room",
)


# A class name is the only exception-derived string allowed into a log record.
# Anything that is not a plain identifier is generalized: an attacker-controlled
# exception message, cause or dynamically built class name must never reach Loki.
_SAFE_TYPE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_GENERIC_ERROR_TYPE = "Exception"
_GENERIC_ERROR_CODE = "unhandled_error"
_ERROR_CODES = {
    "CancelledError": "cancelled",
    "ConnectionError": "dependency_unavailable",
    "DBAPIError": "dependency_unavailable",
    "IntegrityError": "constraint_violation",
    "KeyError": "missing_key",
    "OperationalError": "dependency_unavailable",
    "OSError": "io_error",
    "RuntimeError": "operation_failed",
    "TimeoutError": "dependency_timeout",
    "TypeError": "invalid_type",
    "ValueError": "invalid_value",
}


def safe_error_type(error: object) -> str:
    """Return a bounded error_type; never read the exception text or traceback."""
    if isinstance(error, str):
        name = error
    elif isinstance(error, type):
        name = error.__name__ if issubclass(error, BaseException) else ""
    elif isinstance(error, BaseException):
        name = type(error).__name__
    else:
        name = ""
    return name if _SAFE_TYPE_NAME.fullmatch(name) else _GENERIC_ERROR_TYPE


def error_code_for(error_type: str) -> str:
    """Map a bounded error_type to a stable, low-cardinality error_code."""
    return _ERROR_CODES.get(error_type, _GENERIC_ERROR_CODE)


class JsonFormatter(logging.Formatter):
    """Formats application events as JSON Lines for Loki."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
        }
        for field_name in SAFE_LOG_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value

        # An exc_info may arrive from a third-party library. Classify it by type
        # only: the message, chained causes, traceback frames and their locals
        # are deliberately never inspected or serialized.
        if record.exc_info:
            error_type = safe_error_type(record.exc_info[0])
            payload.setdefault("error_type", error_type)
            payload.setdefault("error_code", error_code_for(error_type))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_application_logging(log_file: str) -> None:
    """Configure the single JSON application log in the given file."""
    for name in ("granian.access", "_granian"):
        transport_logger = logging.getLogger(name)
        if not any(isinstance(item, CredentialQueryFilter) for item in transport_logger.filters):
            transport_logger.addFilter(CredentialQueryFilter())
    logger = logging.getLogger("reportcollector")
    if logger.handlers:
        return

    destination = Path(log_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A plain FileHandler grows without bound: the dev log reached 174 MB in 33
    # hours (~63 GB/year), and Loki's retention only covers what it INGESTED, not
    # the file on disk. The rotation keeps 5 x 64 MB = at most 320 MB, which
    # still holds days of history while capping the disk.
    #
    # `RotatingFileHandler` RENAMES the active file on rollover, and Alloy tails
    # it by path: a rename would silently stop the tail. The `delay`/`backupCount`
    # alone do not solve that — Alloy re-reads the path on `sync_period` (5s) and
    # the renamed file stops matching `*.log`, so at most a few seconds of the
    # tail are at risk, and the new file is picked up from its start because
    # `tail_from_end = false`. Losing a handful of lines at a rollover is an
    # acceptable price for a bounded disk; dropping whole days is not.
    handler = RotatingFileHandler(
        destination,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(logger: logging.Logger, event: str, message: str, **fields: object) -> None:
    """Record a structured event without boilerplate at call sites."""
    logger.info(message, extra={"event": event, **fields})


def log_warning(logger: logging.Logger, event: str, message: str, **fields: object) -> None:
    """Record a warning."""
    logger.warning(message, extra={"event": event, **fields})


def log_error(logger: logging.Logger, event: str, message: str, **fields: object) -> None:
    """Record an error."""
    logger.error(message, extra={"event": event, **fields})


def log_failure(
    logger: logging.Logger,
    event: str,
    message: str,
    error: BaseException,
    *,
    error_code: str | None = None,
    **fields: object,
) -> None:
    """
    Record a failure as a stable classification, never as exception text.

    Callers pass a static ``message``; the exception itself is reduced to a
    bounded ``error_type`` and ``error_code``. No ``exc_info`` is attached, so
    neither this record nor a future formatter change can emit a traceback.
    """
    error_type = safe_error_type(error)
    logger.error(
        message,
        extra={
            "event": event,
            "error_type": error_type,
            "error_code": error_code or error_code_for(error_type),
            **fields,
        },
    )


def monotonic_ms() -> float:
    """Return the current monotonic time in milliseconds."""
    return time.monotonic() * 1000
