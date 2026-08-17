"""
Константы совместного редактирования.

Журнал правок (стек undo/redo) и состояние ячеек живут в Valkey
(rc:undo:{user}, rc:cellstate:*), в Postgres не хранятся.
"""

from uuid import UUID

MOVEMENT_TABLE = "rm_material_movements"
PRICE_TABLE = "rm_raw_material_prices"


def canonical_key(target_table: str, row_id: UUID, field: str) -> str:
    """Return the single cell key: table/row/field."""
    return f"{target_table}/{row_id}/{field}"
