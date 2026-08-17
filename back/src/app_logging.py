import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
        # All extra fields passed via extra={}
        for field_name in (
            "event",
            "duration_ms",
            "error",
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
            "path",
            "status_code",
            "user_email",
            "user_name",
            "user_sub",
            "user_is_admin",
            "oidc_issuer",
            "preferred_username",
            "given_name",
            "family_name",
            "email_verified",
            "bootstrap_is_admin",
            "template_row_id",
            "record_id",
            "catalog_db_name",
            "project_key",
            "room",
            "extra_origin",
        ):
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_application_logging(log_file: str) -> None:
    """Configure the single JSON application log in the given file."""
    logger = logging.getLogger("reportcollector")
    if logger.handlers:
        return

    destination = Path(log_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(destination, encoding="utf-8")
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


def monotonic_ms() -> float:
    """Return the current monotonic time in milliseconds."""
    return time.monotonic() * 1000
