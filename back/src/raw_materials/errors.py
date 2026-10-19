"""Shared errors for raw-material dictionary and cell operations."""


class CellValidationError(ValueError):
    """Invalid cell (field name or value)."""


class DictionaryReferenceError(ValueError):
    """Referential-integrity violation when changing a dictionary."""


DICT_CREATED_SNAPSHOT_INVALID = CellValidationError("Некорректный снимок записи.")
