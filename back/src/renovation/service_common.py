"""
Shared renovation-service primitives: exceptions, error messages, plan-row read helpers and renovation-ID helpers.

This module imports nothing from ``service``/``plan_reads`` so the dependency
direction stays one-way (Phase 4 decomposition). All values and behaviour are
moved verbatim from ``service.py``.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.raw_materials.service import CellValidationError
from src.renovation.form_schema import (
    RenovationFieldMeta,
    field_metas,
)
from src.renovation.models import RAsset, RBlock
from src.renovation.plan import (
    RenovationPlan,
    RenovationPlanRow,
    RenovationReferenceValue,
    RPlanState,
)
from src.renovation.read_cache import reference_labels as cached_reference_labels
from src.renovation.renovation_id import (
    MISSING_COMPONENT,
    RENOVATION_ID_FORMULA_ENABLED,
    RenovationIdError,
    RenovationIdInput,
    RenovationIdResult,
    calculate_renovation_id,
)
from src.renovation.schemas import (
    RenovationPlanRowRead,
)

from .errors import RenovationPlanNotFoundError


class NoRenovationAccessError(Exception):
    """The user has no access to the «Ремонт» project."""


class RenovationPermissionError(ValueError):
    """The user's role/block doesn't allow the requested operation."""


class RenovationPlanStateError(ValueError):
    """The plan lifecycle doesn't allow the requested transition."""


# Typical error messages (module constants + factories — project style).
# col_002 (asset) has no row column: the asset comes from the row's
# asset_id (a virtual field; read-only).
_VIRTUAL_FIELDS = frozenset({"col_002"})

# The audit trio the plan grid shows when «Показать служебные столбцы» is on.
# They are NOT form fields (no `col_*` column, no cell type in the workbook), but
# they ARE real row columns, so a header filter treats them like any other:
# `createdAt`/`updatedAt` are instants (timestamptz, filtered by an exact
# rendered minute), `updatedBy` is the display name of `updater_user_id`.
_PLAN_AUDIT_COLUMNS: dict[str, str] = {
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}


def _plan_audit_attr(column: str) -> InstrumentedAttribute[object] | None:
    """
    Return the row attribute behind an audit column prop (None: not one).

    These ARE column attributes at runtime (SQLAlchemy's descriptor); the model
    declares them as plain fields, so the cast restates the runtime type instead
    of widening the signature to `Any`.
    """
    if column == "updatedBy":
        return cast("InstrumentedAttribute[object]", RenovationPlanRow.updater_user_id)
    name = _PLAN_AUDIT_COLUMNS.get(column)
    return cast("InstrumentedAttribute[object]", getattr(RenovationPlanRow, name)) if name is not None else None


# Rows per streamed export batch (see `_row_batches` in read_plan_export_data).
EXPORT_BATCH_SIZE = 5000
# The synthetic «Предприятие» column prop the grid uses for the asset name
# (there is no matching row column — it is joined from r_assets).
_ASSET_COLUMN_PROP = "__assetName"

# Validation presets accompany the concrete code list.
_RENOVATION_ID_COLUMN = "work_or_repair_code"
_RENOVATION_ID_OK_LABEL = "Корректные"
_RENOVATION_ID_BAD_LABEL = "Некорректные"

# Identification-code search must fold case on PostgreSQL 14 (production) as
# well as 18 (CI). `casefold()` and the `pg_unicode_fast` collation exist only
# in 18, and `lower()`'s mapping follows the database collation — a `C`
# database would leave Cyrillic uppercase untouched. `translate` is
# locale-independent and covers the identifiers' alphabet (ASCII + Russian
# Cyrillic); the sharp s expands like Python's `str.casefold`.
_ID_FOLD_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
_ID_FOLD_LOWER = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщъыьэюя"

_CELL_MISSING_ROW_OR_FIELD = ValueError("Ячейка не содержит идентификатор строки или поля.")
_CELL_PLAN_ROW_NOT_FOUND = ValueError("Строка плана не найдена.")
_ERR_NO_WORKING_PLAN = RenovationPlanNotFoundError("Нет рабочего плана.")
_ERR_NO_PENDING_PLAN = RenovationPlanNotFoundError("Нет плана на согласовании.")
_ERR_PLAN_NOT_FOUND = RenovationPlanNotFoundError("План не найден.")
_ERR_UNKNOWN_TABLE = CellValidationError("Неизвестная таблица для ячеек плана.")
_ERR_SUBMIT_PERMISSION = RenovationPermissionError(
    "Отправить план на согласование может администратор или владелец блока."
)
_ERR_APPROVE_PERMISSION = RenovationPermissionError("Утвердить план может утверждающий.")
_ERR_EDIT_PENDING = RenovationPermissionError("Версию на согласовании редактировать может только администратор.")
_ERR_ADD_PERMISSION = RenovationPermissionError(
    "Добавлять строки может администратор или работник Технической дирекции по активу."
)
_ERR_ADD_ASSET = RenovationPermissionError("Нельзя добавить строку по этому активу.")
_ERR_DELETE_BASELINE_ROW = RenovationPlanStateError("Нельзя удалить строку утверждённой базовой линии.")
_ERR_DELETE_APPROVER = RenovationPermissionError("Утверждающий не удаляет строки плана.")
_ERR_ROW_HAS_ASSET = RenovationPlanStateError("У строки уже указан актив.")
_ERR_DELETE_NOT_EMPTY = RenovationPlanStateError("Строку с заполненными данными удалить нельзя — очистите её сначала.")
_ERR_DIFF_ARCHIVED_ONLY = RenovationPlanStateError("Сравнение доступно только для архивных версий.")


def _unknown_field(field: str) -> CellValidationError:
    return CellValidationError(f"Неизвестное поле «{field}» формы «Ремонт».")


def _read_only_field(field: str) -> CellValidationError:
    return CellValidationError(f"Поле «{field}» формы «Ремонт» доступно только для чтения.")


def _approver_read_only(field: str) -> RenovationPermissionError:
    return RenovationPermissionError(f"Утверждающий не редактирует план (поле «{field}»).")


def _block_access(field: str, block: RBlock) -> RenovationPermissionError:
    return RenovationPermissionError(f"Нет доступа к полю «{field}» блока «{block.value}».")


def _asset_access() -> RenovationPermissionError:
    return RenovationPermissionError("Нет доступа к строке плана этого актива.")


def _reference_value(catalog: str, label: str) -> CellValidationError:
    return CellValidationError(f"Значение «{label}» справочника «{catalog}» не найдено.")


def _bad_date(value: object) -> CellValidationError:
    return CellValidationError(f"Не дата: {value!r}")


def _bad_boolean(value: object) -> CellValidationError:
    return CellValidationError(f"Не булево значение: {value!r}")


def _rule_violation(label: str, detail: str) -> CellValidationError:
    return CellValidationError(f"Значение в ячейке «{label}» нарушает ограничение: {detail}.")


async def _rows_by_position(
    session: AsyncSession,
    plan_id: UUID,
    assets: list[RAsset],
) -> dict[int, tuple[RenovationPlanRow, RAsset]]:
    rows = (
        await session.exec(
            select(RenovationPlanRow, RAsset)
            .join(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
            .where(col(RenovationPlanRow.plan_id) == plan_id)
            .where(col(RenovationPlanRow.asset_id).in_([asset.id for asset in assets]))
            .order_by(col(RenovationPlanRow.position))
        )
    ).all()
    return {row.position: (row, asset) for row, asset in rows}


async def _latest_archived_plan(session: AsyncSession) -> RenovationPlan | None:
    return (
        await session.exec(
            select(RenovationPlan)
            .where(col(RenovationPlan.state) == RPlanState.ARCHIVED)
            .order_by(col(RenovationPlan.created_at).desc())
        )
    ).first()


async def _previous_archived_plan(session: AsyncSession, before: datetime) -> RenovationPlan | None:
    return (
        await session.exec(
            select(RenovationPlan)
            .where(col(RenovationPlan.state) == RPlanState.ARCHIVED, col(RenovationPlan.created_at) < before)
            .order_by(col(RenovationPlan.created_at).desc())
        )
    ).first()


async def _baseline_boundary(session: AsyncSession) -> int | None:
    """Return the max row position of the latest archived plan (the working baseline)."""
    plan = await _latest_archived_plan(session)
    if plan is None:
        return None
    max_position = (
        await session.exec(
            select(func.max(col(RenovationPlanRow.position))).where(col(RenovationPlanRow.plan_id) == plan.id)
        )
    ).one()
    return int(max_position) if max_position is not None else None


def _plan_row_read(
    row: RenovationPlanRow,
    asset: RAsset | None,
    label_map: dict[UUID, str],
) -> RenovationPlanRowRead:
    values: dict[str, str | float | bool | date | None] = {}
    for meta in field_metas():
        if meta.db_name in _VIRTUAL_FIELDS:
            # The synthetic «Предприятие» column is blank until an asset is chosen.
            values[meta.db_name] = asset.name if asset is not None else ""
            continue
        value = getattr(row, meta.db_name)
        if value is None:
            # Omit empty cells. The plan is ~90% empty, so the null placeholders
            # dominate the payload: a 1000-row window measures ~4.2 MB with every
            # key present and ~0.9 MB without. The grid renders a missing key
            # exactly like an explicit null, so the response stays equivalent.
            continue
        if meta.cell_type == "reference":
            value = label_map.get(value)
        elif meta.cell_type == "number":
            value = float(value) if isinstance(value, Decimal) else value
        elif meta.cell_type == "boolean":
            value = bool(value)
        if value is None:
            # A reference id with no label resolves to nothing — still sparse.
            continue
        values[meta.db_name] = value
    renovation_id, missing = _row_renovation_id(row, asset, label_map)
    return RenovationPlanRowRead(
        id=row.id,
        assetId=asset.id if asset is not None else None,
        assetCode=asset.code if asset is not None else None,
        assetName=asset.name if asset is not None else None,
        assetRepairCode=asset.code if asset is not None else None,
        position=row.position,
        revision=row.revision,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
        updatedBy=row.updated_by,
        values=values,
        renovationId=renovation_id,
        missingComponents=list(missing),
        invalidFields=_row_rule_violations(row, label_map),
    )


def _row_rule_violations(
    row: RenovationPlanRow,
    label_map: dict[UUID, str],
) -> list[str]:
    """
    Fields whose stored value breaks a form rule (for the grid's red highlight).

    Rules from the source workbook (minimum/percentage/allowed_values/integer)
    are enforced on input by `_coerce_cell`, but a CALCULATED field is never
    edited directly: e.g. «Продолжительность, дни» = work_end_date − work_start_date can go
    negative when the dates are entered in the wrong order. Reporting the
    offending fields lets the grid tint those cells red, like the incomplete
    renovation ID.
    """
    invalid: list[str] = []
    for meta in field_metas():
        if meta.db_name in _VIRTUAL_FIELDS or not meta.rules:
            continue
        value = getattr(row, meta.db_name)
        if value is None:
            continue
        if meta.cell_type == "reference":
            value = label_map.get(value)
            if value is None:
                continue
        if _value_breaks_rules(meta, value):
            invalid.append(meta.db_name)
    return invalid


def _value_breaks_rules(meta: RenovationFieldMeta, value: object) -> bool:
    try:
        _check_edit_rules(meta, value)
    except CellValidationError:
        return True
    return False


def _safe_renovation_id(input_: RenovationIdInput) -> RenovationIdResult:
    """
    Compute the renovation ID, tolerating invalid components.

    The source spreadsheets carry values that violate the legacy rules (a
    defect number that is not seven digits, a malformed direction code, …). A
    bad row must not break the whole plan read: the ID falls back to the
    `<NULL>` placeholder and is flagged via `missing_components`.
    """
    try:
        return calculate_renovation_id(input_)
    except RenovationIdError:
        return RenovationIdResult(value=MISSING_COMPONENT, missing_components=("invalid_components",))


def _row_renovation_id(
    row: RenovationPlanRow,
    asset: RAsset | None,
    label_map: dict[UUID, str],
) -> tuple[str | None, tuple[str, ...]]:
    """
    Return the stored renovation ID, previewing the generator when it is empty.

    With the generator OFF the preview would show a code the database does not
    hold — the cell must read as what will be saved, so an empty `work_or_repair_code` stays
    empty. The generator stays for the day it is switched back on.
    """
    if row.work_or_repair_code:
        return row.work_or_repair_code, ()
    if asset is None or not RENOVATION_ID_FORMULA_ENABLED:
        return None, ()
    result = _safe_renovation_id(_renovation_id_input(row, asset, label_map))
    return result.value, result.missing_components


# The cells `calculate_renovation_id` reads. Editing any of them must rebuild
# work_or_repair_code from the row's inputs instead of keeping the stored value — but only
# while the generator is ON (`RENOVATION_ID_FORMULA_ENABLED`).
_RENOVATION_ID_INPUT_FIELDS = frozenset(
    {
        "process_position",
        "bpof_section",
        "work_category_id",
        "work_type_id",
        "defect_list_or_bill_of_quantities_number",
        "work_start_date",
        "plan_adjustment_type",
        "lotting_activity_direction_code",
    }
)


def defect_number_text(value: object) -> str | None:
    """
    Return the 7-digit defect-statement number as text.

    The column is TEXT now, but the value still arrives as a float from Excel or
    as digits-without-zero from an older number cell: a dropped leading zero is
    restored (`706179` → `0706179`), and a numeric tail is dropped (`7.0` →
    `7`). The legacy id expects exactly seven digits; anything that is not a
    plain number is kept verbatim.
    """
    if value is None:
        return None
    text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
    if text.isdigit() and len(text) < 7:
        return text.zfill(7)
    return text


def _renovation_id_input(
    row: RenovationPlanRow,
    asset: RAsset,
    label_map: dict[UUID, str],
    *,
    keep_existing: bool = True,
) -> RenovationIdInput:
    """
    Row inputs for the renovation ID (legacy: project_code/suffix always None).

    `keep_existing` preserves an already-assigned id (the import/backfill and
    unrelated edits); pass False when the edit changed one of the id inputs so
    the id is rebuilt from them.
    """
    return RenovationIdInput(
        existing_id=row.work_or_repair_code if keep_existing else None,
        start_date=row.work_start_date.isoformat() if isinstance(row.work_start_date, date) else None,
        asset_repair_code=asset.code,
        direction_code=row.lotting_activity_direction_code,
        bpof_section=row.bpof_section,
        work_category=label_map.get(row.work_category_id) if row.work_category_id is not None else None,
        project_code=row.plan_adjustment_type,
        defect_number=defect_number_text(row.defect_list_or_bill_of_quantities_number),
        technology_position=row.process_position,
        work_type_label=label_map.get(row.work_type_id) if row.work_type_id is not None else None,
    )


async def _resolve_reference_id(session: AsyncSession, catalog: str | None, label: str | None) -> UUID | None:
    """Resolve the reference value id by display label (''/None → no value)."""
    if label is None or catalog is None:
        return None
    value = (
        await session.exec(
            select(RenovationReferenceValue).where(
                col(RenovationReferenceValue.catalog) == catalog,
                col(RenovationReferenceValue.label) == label,
            )
        )
    ).first()
    if value is None:
        raise _reference_value(catalog, label)
    return value.id


async def _reference_labels(session: AsyncSession) -> dict[UUID, str]:
    """Labels of the reference catalogue (cached until a catalogue row changes)."""
    return await cached_reference_labels(session)


def _row_datetime(value: object) -> datetime:
    """Parse a journaled ISO timestamp; the current time when it is unusable."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _row_value_label(
    row: RenovationPlanRow,
    meta: RenovationFieldMeta,
    label_map: dict[UUID, str],
) -> str | float | bool | None:
    """Display value of a field (reference → label, date → ISO string) for the journal/response."""
    value = getattr(row, meta.db_name)
    if value is None:
        return None
    if meta.cell_type == "reference":
        return label_map.get(value)
    if meta.cell_type == "number":
        return float(value) if isinstance(value, Decimal) else value
    if meta.cell_type == "date":
        # Dates must stay JSON-safe: the journal, undo stack and the collab
        # cell_changed payload are all plain-JSON (json.dumps has no date
        # default), and the frontend grid cells are ISO strings anyway.
        return value.isoformat()
    if meta.cell_type == "boolean":
        return bool(value)
    return value


def _is_field_rule(rule: dict[str, object], kind: str) -> bool:
    return rule.get("kind") == kind


def _percentage_bounds(rule: dict[str, object]) -> tuple[Decimal, Decimal]:
    minimum = Decimal(str(rule.get("minimum", 0)))
    maximum = Decimal(str(rule.get("maximum", 100)))
    return minimum, maximum


def _check_edit_rules(meta: RenovationFieldMeta, value: object) -> None:
    """
    Validate a value against the form's field-local rules before it is stored.

    Only rules checkable from the cell value itself are enforced here (values
    are non-null: `None` is allowed by `allowNull` and clears the cell):
    `minimum`, `integer`, `percentage` and `allowed_values`. The cross-field
    rules (`field_compare` — work_end_date ≥ work_start_date; `calendar_year` — work_start_date) are
    validated in the grid, which has both the field metadata and the row.
    """
    if value is None:
        return
    for rule in meta.rules:
        kind = rule.get("kind")
        if kind == "minimum":
            minimum = rule.get("value")
            if minimum is not None and Decimal(str(value)) < Decimal(str(minimum)):
                raise _rule_violation(meta.label, "значение не может быть отрицательным")
        elif kind == "integer":
            if Decimal(str(value)) != Decimal(str(value)).to_integral_value():
                raise _rule_violation(meta.label, "значение должно быть целым числом")
        elif kind == "percentage":
            minimum, maximum = _percentage_bounds(rule)
            if not (minimum <= Decimal(str(value)) <= maximum):
                raise _rule_violation(meta.label, f"значение должно быть от {minimum} до {maximum}%")
        elif kind == "allowed_values":
            allowed = rule.get("values")
            if isinstance(allowed, list) and str(value) not in {str(item) for item in allowed}:
                raise _rule_violation(meta.label, "значение не входит в список допустимых")


def _check_date_rules(meta: RenovationFieldMeta, value: date | None) -> None:
    """Date rules needing the row are checked in the grid; nothing here yet."""
    del meta, value


def _coerce_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as error:
            raise _bad_date(value) from error
    raise _bad_date(value)
