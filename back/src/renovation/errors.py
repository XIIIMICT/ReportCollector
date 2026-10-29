"""Domain errors shared by renovation plan and dictionary services."""


class RenovationDictionaryError(ValueError):
    """Invalid or conflicting dictionary operation (e.g. a used option)."""


class RenovationPlanNotFoundError(ValueError):
    """The requested plan version doesn't exist."""


class PlanExportRequiresJobError(RuntimeError):
    """The selected plan rows exceed the bounded inline export budget."""
