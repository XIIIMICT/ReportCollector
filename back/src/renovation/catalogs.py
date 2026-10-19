"""Reference catalog reads, mutations and deletion replay for renovation."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.raw_materials.service import CellValidationError
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.form_schema import field_metas
from src.renovation.plan import RenovationPlanRow, RenovationReferenceValue, RReferenceCatalog
from src.renovation.schemas import RenovationCatalogOptionRowRead

from .errors import RenovationDictionaryError

if TYPE_CHECKING:
    from src.auth.models import User

_DICT_OPTION_NOT_FOUND = ValueError("Значение каталога не найдено.")
_DICT_OPTION_USED = RenovationDictionaryError("Нельзя удалить: значение используется в плане ремонта.")
_DICT_OPTION_LABEL_EMPTY = RenovationDictionaryError("Название значения не может быть пустым.")


async def read_renovation_catalog_rows(
    session: AsyncSession,
    *,
    catalog: str,
    option_id: UUID | None = None,
) -> list[RenovationCatalogOptionRowRead]:
    """Options of a renovation reference catalog (ordered by sort_order, label)."""
    query = (
        select(RenovationReferenceValue)
        .where(col(RenovationReferenceValue.catalog) == catalog)
        .order_by(col(RenovationReferenceValue.sort_order), col(RenovationReferenceValue.label))
    )
    if option_id is not None:
        query = query.where(col(RenovationReferenceValue.id) == option_id)
    rows = (await session.exec(query)).all()
    return [
        RenovationCatalogOptionRowRead(
            valueId=str(option.id),
            label=option.label,
            createdAt=option.created_at,
            updatedAt=option.updated_at,
            updatedBy=option.updated_by,
        )
        for option in rows
    ]


async def _ensure_option_label_free(
    session: AsyncSession,
    *,
    catalog: str,
    label: str,
    exclude_id: UUID | None,
) -> None:
    """Reject a duplicate label within one catalog (case-insensitive)."""
    query = select(RenovationReferenceValue).where(
        col(RenovationReferenceValue.catalog) == RReferenceCatalog(catalog),
        func.lower(col(RenovationReferenceValue.label)) == label.lower(),
    )
    if exclude_id is not None:
        query = query.where(col(RenovationReferenceValue.id) != exclude_id)
    duplicate = (await session.exec(query)).first()
    if duplicate is not None:
        message = f"Запись «{label}» уже есть в этом справочнике."
        raise RenovationDictionaryError(message)


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def create_renovation_catalog_option(
    session: AsyncSession,
    *,
    catalog: str,
    label: str,
    user: User,
) -> tuple[RenovationReferenceValue, RenovationCatalogOptionRowRead]:
    """Append an option to a renovation reference catalog."""
    label = label.strip()
    if not label:
        raise _DICT_OPTION_LABEL_EMPTY
    await _ensure_option_label_free(session, catalog=catalog, label=label, exclude_id=None)
    max_order = (
        await session.exec(
            select(func.max(col(RenovationReferenceValue.sort_order))).where(
                col(RenovationReferenceValue.catalog) == catalog
            )
        )
    ).one()
    now = datetime.now(UTC)
    option = RenovationReferenceValue(
        catalog=RReferenceCatalog(catalog),
        value_key=f"{catalog}_{uuid4().hex[:8]}",
        label=label,
        sort_order=int(max_order) + 1 if max_order is not None else 1,
        created_at=now,
        updated_at=now,
        updated_by=user.name or user.email or "",
    )
    session.add(option)
    await session.flush()
    await session.refresh(option)
    row = (await read_renovation_catalog_rows(session, catalog=catalog, option_id=option.id))[0]
    return option, row


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def update_renovation_catalog_option(
    session: AsyncSession,
    *,
    catalog: str,
    option_id: UUID,
    label: str | None,
    user: User,
) -> tuple[RenovationReferenceValue, list[AppliedCell]]:
    """Rename an option of a renovation reference catalog."""
    option = await session.get(RenovationReferenceValue, option_id)
    if option is None or option.catalog.value != catalog:
        raise _DICT_OPTION_NOT_FOUND
    old_label = option.label
    if label is not None:
        label = label.strip()
        if not label:
            raise _DICT_OPTION_LABEL_EMPTY
        await _ensure_option_label_free(session, catalog=catalog, label=label, exclude_id=option_id)
        option.label = label
    option.updated_at = datetime.now(UTC)
    option.updated_by = user.name or user.email or ""
    session.add(option)
    await session.flush()
    applied: list[AppliedCell] = []
    if option.label != old_label:
        applied.append(
            AppliedCell(
                target_table=RenovationReferenceValue.__tablename__,
                row_id=option.id,
                field="label",
                old_value=old_label,
                new_value=option.label,
            )
        )
    return option, applied


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def delete_renovation_catalog_option(
    session: AsyncSession,
    *,
    catalog: str,
    option_id: UUID,
) -> list[AppliedCell]:
    """
    Delete a catalog option; blocked while used by plan rows.

    The deletion is journaled as an undoable «__deleted» cell (same pattern
    as the asset deletion: undo restores the option from the snapshot).
    """
    option = await session.get(RenovationReferenceValue, option_id)
    if option is None or option.catalog.value != catalog:
        raise _DICT_OPTION_NOT_FOUND
    reference_fields = [getattr(RenovationPlanRow, meta.db_name) for meta in field_metas() if meta.catalog == catalog]
    for field in reference_fields:
        # Fetch one scalar instead of materializing a 224-column plan row. A
        # per-reference-column index makes this independent of plan size.
        used = (await session.exec(select(1).where(field == option_id).limit(1))).first()
        if used is not None:
            raise _DICT_OPTION_USED
    snapshot = {
        "catalog": option.catalog.value,
        "valueKey": option.value_key,
        "label": option.label,
        "sortOrder": option.sort_order,
        "payload": option.payload,
    }
    await session.delete(option)
    await session.flush()
    return [
        AppliedCell(
            target_table=RenovationReferenceValue.__tablename__,
            row_id=option_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def _apply_renovation_option_deleted(
    session: AsyncSession,
    row_id: UUID,
    value: object,
) -> None:
    """Undo/redo of a deleted renovation catalog option (restore / re-delete)."""
    if value is None:
        # Redo: delete the restored option again.
        option = await session.get(RenovationReferenceValue, row_id)
        if option is None:
            return
        await session.delete(option)
        return
    # Undo: restore the option from the snapshot.
    if not isinstance(value, dict):
        message = "Некорректные данные для восстановления значения каталога."
        raise CellValidationError(message)
    try:
        catalog = RReferenceCatalog(str(value.get("catalog") or ""))
    except ValueError as error:
        message = "Некорректный каталог в данных для восстановления."
        raise CellValidationError(message) from error
    option = RenovationReferenceValue(
        id=row_id,
        catalog=catalog,
        value_key=str(value.get("valueKey") or ""),
        label=str(value.get("label") or ""),
        sort_order=int(value.get("sortOrder") or 0),
        payload=value.get("payload") if value.get("payload") is not None else None,
        is_active=True,
    )
    session.add(option)
