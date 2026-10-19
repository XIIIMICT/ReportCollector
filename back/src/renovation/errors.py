"""Domain errors shared by renovation plan and dictionary services."""


class RenovationDictionaryError(ValueError):
    """Invalid or conflicting dictionary operation (e.g. a used option)."""
