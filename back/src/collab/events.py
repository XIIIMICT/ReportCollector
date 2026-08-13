"""
Публикация событий совместного редактирования в комнаты.

Комната: rm:{project}:{section}:{reportMonth} (для «Цен» reportMonth отсутствует).
Проект входит в ключ комнаты: правки разных проектов не смешиваются.
События публикуются в общий канал комнаты: клиенты всех воркеров получают
их через pub/sub и применяют к гриду.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from src.collab.store import CollabStore, now_iso

if TYPE_CHECKING:
    from src.collab.journal import AppliedCell


def room_key(project_key: str, section: str, report_month: str | None = None) -> str:
    return f"rm:{project_key}:{section}:{report_month or ''}"


async def publish_cell_changed(
    store: CollabStore,
    *,
    project_key: str,
    section: str,
    report_month: str | None,
    user_id: UUID,
    user_name: str,
    user_email: str,
    cells: list[AppliedCell],
) -> None:
    """Расслылает правки ячеек после успешного commit."""
    room = room_key(project_key, section, report_month)
    for cell in cells:
        await store.publish(
            room,
            {
                "type": "cell:changed",
                "project": project_key,
                "section": section,
                "reportMonth": report_month,
                "rowId": str(cell.row_id),
                "field": cell.field,
                "value": cell.new_value,
                "revision": cell.revision,
                "actionId": cell.action_id,
                "user": {"userId": str(user_id), "name": user_name, "email": user_email},
                "at": now_iso(),
            },
        )
