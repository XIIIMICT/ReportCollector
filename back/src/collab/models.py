"""
Константы совместного редактирования.

Журнал правок (стек undo/redo) и состояние ячеек живут в Valkey
(rc:undo:{user}, rc:cellstate:*), в Postgres не хранятся.
"""

from uuid import UUID

MOVEMENT_TABLE = "rm_material_movement"
PRICE_TABLE = "rm_raw_material_price"


def canonical_key(target_table: str, row_id: UUID, field: str) -> str:
    """Единственный ключ ячейки: таблица/строка/поле."""
    return f"{target_table}/{row_id}/{field}"
