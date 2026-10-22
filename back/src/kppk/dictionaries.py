"""KPPK source dictionary reads, mutations and undo replay."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.kppk.constants import KPPK_PROJECT_KEY
from src.kppk.models import (
    KppkCost,
    KppkOrg,
    KppkOrgProduct,
    KppkProduct,
    KppkRaw,
    KppkRealization,
    KppkRealizationPlan,
)
from src.kppk.schemas import (
    KppkOrgRowRead,
    KppkProductPositionCreateRequest,
    KppkProductPositionRowRead,
    KppkRawRowRead,
)

from .core import KppkNotFoundError, KppkValidationError, _now
from .realization import apply_kppk_realization_cell_value, update_cost_cell

if TYPE_CHECKING:
    from src.auth.models import User


def _check_source_number(value: object) -> int | None:
    """Parse an editable source number («№») of a copy; empty clears it (NULL)."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        number = int(text)
    except (TypeError, ValueError) as error:
        message = "Номер — целое неотрицательное число."
        raise KppkValidationError(message) from error
    if number < 0:
        message = "Номер — целое неотрицательное число."
        raise KppkValidationError(message)
    return number


async def read_product_position_rows(session: AsyncSession) -> list[KppkProductPositionRowRead]:
    """Rows of the «Продуктовые позиции» dictionary (products with their ДЗО)."""
    query = (
        select(KppkProduct, KppkOrgProduct, KppkOrg)
        .outerjoin(KppkOrgProduct, col(KppkOrgProduct.product_id) == col(KppkProduct.id))
        .outerjoin(KppkOrg, col(KppkOrg.id) == col(KppkOrgProduct.org_id))
        # The default order is the source number («№»): the numbered dictionary
        # products first (ASC puts NULLs last in PostgreSQL), then the rest.
        .order_by(col(KppkProduct.product_num), col(KppkProduct.name), col(KppkOrg.name))
    )
    rows = (await session.exec(query)).all()
    grouped: dict[UUID, tuple[KppkProduct, list[str]]] = {}
    for product, _link, org in rows:
        entry = grouped.setdefault(product.id, (product, []))
        if org is not None:
            entry[1].append(org.name)
    return [
        KppkProductPositionRowRead(
            id=product.id,
            productNum=product.product_num,
            name=product.name,
            orgNames=org_names,
            createdAt=product.created_at,
            updatedAt=product.updated_at,
            updatedBy=product.updated_by,
        )
        for product, org_names in (entry for _, entry in grouped.items())
    ]


def _check_product_name(name: object) -> str:
    value = " ".join(str(name or "").split())
    if not value:
        message = "Укажите наименование продукта."
        raise KppkValidationError(message)
    if len(value) > 255:
        message = "Наименование продукта слишком длинное (до 255 символов)."
        raise KppkValidationError(message)
    return value


def _check_org_names(value: object) -> list[str]:
    """Validate an `orgNames` cell value (a list of non-empty display names)."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = "Некорректный список ДЗО."
        raise KppkValidationError(message)
    return [item.strip() for item in value if item.strip()]


async def _product_by_name(session: AsyncSession, name: str) -> KppkProduct | None:
    """Case-insensitive product lookup."""
    products = (await session.exec(select(KppkProduct))).all()
    return next((product for product in products if product.name.lower() == name.lower()), None)


async def _org_ids_by_names(session: AsyncSession, names: list[str]) -> list[UUID]:
    """ДЗО ids by their display names (the names the matrix columns carry)."""
    if not names:
        return []
    rows = (await session.exec(select(KppkOrg).where(col(KppkOrg.name).in_(names)))).all()
    by_name = {row.name: row.id for row in rows}
    missing = [name for name in names if name not in by_name]
    if missing:
        message = f"ДЗО не найдены: {', '.join(missing)}"
        raise KppkValidationError(message)
    return [by_name[name] for name in names]


async def replace_org_links(
    session: AsyncSession,
    product_id: UUID,
    org_ids: list[UUID],
    *,
    updater_user_id: UUID | None,
) -> None:
    """Replace the ДЗО links of a product by diff (same mechanics as in «Сырьё»)."""
    existing = {
        link.org_id: link
        for link in (
            await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == product_id))
        ).all()
    }
    existing_ids = set(existing)
    for org_id in existing_ids - set(org_ids):
        await session.delete(existing[org_id])
    # Deletions flush before inserts: the links have a unique (org_id, product_id) key.
    await session.flush()
    now = _now()
    for org_id in org_ids:
        if org_id not in existing_ids:
            session.add(
                KppkOrgProduct(
                    org_id=org_id,
                    product_id=product_id,
                    created_at=now,
                    updated_at=now,
                    updater_user_id=updater_user_id,
                )
            )
    await session.flush()


async def _product_org_names(session: AsyncSession, product_id: UUID) -> list[str]:
    """Return the current ДЗО display names of a product (the read order: by name)."""
    names = (
        await session.exec(
            select(KppkOrg.name)
            .join(KppkOrgProduct, col(KppkOrgProduct.org_id) == col(KppkOrg.id))
            .where(col(KppkOrgProduct.product_id) == product_id)
            .order_by(col(KppkOrg.name))
        )
    ).all()
    return list(names)


async def _product_field_value(session: AsyncSession, product: KppkProduct, field: str) -> object:
    """Return the current journal value of one product cell (the undo «old value»)."""
    if field == "orgNames":
        return await _product_org_names(session, product.id)
    if field == "name":
        return product.name
    if field == "productNum":
        return product.product_num
    message = f"Поле «{field}» нельзя изменить."
    raise KppkValidationError(message)


async def _apply_product_cell(
    session: AsyncSession,
    *,
    product: KppkProduct,
    field: str,
    value: object,
    user: User,
) -> object:
    """Validate and store one product cell; returns the normalized journal value."""
    if field == "name":
        product_name = _check_product_name(value)
        rows = (await session.exec(select(KppkProduct).where(col(KppkProduct.id) != product.id))).all()
        if any(row.name.lower() == product_name.lower() for row in rows):
            message = f"Продукт «{product_name}» уже существует."
            raise KppkValidationError(message)
        product.name = product_name
        stored: object = product_name
    elif field == "orgNames":
        org_names = _check_org_names(value)
        org_ids = await _org_ids_by_names(session, org_names)
        await replace_org_links(session, product.id, org_ids, updater_user_id=user.id)
        stored = sorted(org_names)
    elif field == "productNum":
        number = _check_source_number(value)
        if number is not None:
            duplicate = (
                await session.exec(
                    select(KppkProduct).where(
                        col(KppkProduct.product_num) == number,
                        col(KppkProduct.id) != product.id,
                    )
                )
            ).first()
            if duplicate is not None:
                message = f"Номер {number} уже используется: «{duplicate.name}»."
                raise KppkValidationError(message)
        product.product_num = number
        stored = number
    else:
        message = f"Поле «{field}» нельзя изменить."
        raise KppkValidationError(message)
    product.updated_at = _now()
    product.updater_user_id = user.id
    session.add(product)
    await session.flush()
    return stored


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def create_product_position(
    session: AsyncSession,
    *,
    request: KppkProductPositionCreateRequest,
    user: User,
) -> tuple[KppkProductPositionRowRead, list[AppliedCell]]:
    """Create a product with its ДЗО set (one row of «Продуктовые позиции»)."""
    product_name = _check_product_name(request.name)
    if await _product_by_name(session, product_name) is not None:
        message = f"Продукт «{product_name}» уже существует."
        raise KppkValidationError(message)
    product_num = _check_source_number(request.productNum)
    if product_num is not None:
        duplicate = (await session.exec(select(KppkProduct).where(col(KppkProduct.product_num) == product_num))).first()
        if duplicate is not None:
            message = f"Номер {product_num} уже используется: «{duplicate.name}»."
            raise KppkValidationError(message)
    org_names = _check_org_names(request.orgNames)
    org_ids = await _org_ids_by_names(session, org_names)
    product = KppkProduct(
        product_num=product_num,
        name=product_name,
        created_at=_now(),
        updated_at=_now(),
        updater_user_id=user.id,
    )
    session.add(product)
    await session.flush()
    await replace_org_links(session, product.id, org_ids, updater_user_id=user.id)
    row = KppkProductPositionRowRead(
        id=product.id,
        productNum=product.product_num,
        name=product.name,
        orgNames=sorted(org_names),
        createdAt=product.created_at,
        updatedAt=product.updated_at,
        updatedBy=product.updated_by,
    )
    applied = [
        AppliedCell(
            target_table=KppkProduct.__tablename__,
            row_id=product.id,
            field="__created",
            old_value=None,
            new_value={"productNum": row.productNum, "name": row.name, "orgNames": row.orgNames},
        )
    ]
    return row, applied


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_product_position_cell(
    session: AsyncSession,
    *,
    product_id: UUID,
    field: str,
    value: object,
    user: User,
) -> list[AppliedCell]:
    """Edit one cell of a «Продуктовые позиции» row (name / orgNames / productNum)."""
    product = await session.get(KppkProduct, product_id)
    if product is None:
        message = "Позиция не найдена."
        raise KppkNotFoundError(message)
    old_value = await _product_field_value(session, product, field)
    stored = await _apply_product_cell(session, product=product, field=field, value=value, user=user)
    return [
        AppliedCell(
            target_table=KppkProduct.__tablename__,
            row_id=product_id,
            field=field,
            old_value=old_value,
            new_value=stored,
        )
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def delete_product_position(session: AsyncSession, *, product_id: UUID) -> list[AppliedCell]:
    """Delete a product together with its ДЗО links."""
    product = await session.get(KppkProduct, product_id)
    if product is None:
        message = "Позиция не найдена."
        raise KppkNotFoundError(message)
    snapshot: dict[str, object] = {
        "productNum": product.product_num,
        "name": product.name,
        "orgNames": await _product_org_names(session, product_id),
    }
    links = (await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == product_id))).all()
    for link in links:
        await session.delete(link)
    # Deletions flush before the product delete: the RESTRICT key guards the order.
    await session.flush()
    await session.delete(product)
    await session.flush()
    return [
        AppliedCell(
            target_table=KppkProduct.__tablename__,
            row_id=product_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def read_raw_rows(session: AsyncSession) -> list[KppkRawRowRead]:
    """Rows of the «Сырьевые позиции» dictionary — in the source-number order."""
    raws = (await session.exec(select(KppkRaw).order_by(col(KppkRaw.raw_num), col(KppkRaw.name)))).all()
    return [
        KppkRawRowRead(
            id=raw.id,
            rawNum=raw.raw_num,
            name=raw.name,
            createdAt=raw.created_at,
            updatedAt=raw.updated_at,
            updatedBy=raw.updated_by,
        )
        for raw in raws
    ]


def _raw_field_value(raw: KppkRaw, field: str) -> object:
    """Return the current journal value of one raw cell (the undo «old value»)."""
    if field == "name":
        return raw.name
    if field == "rawNum":
        return raw.raw_num
    message = f"Поле «{field}» нельзя изменить."
    raise KppkValidationError(message)


async def _apply_raw_cell(
    session: AsyncSession,
    *,
    raw: KppkRaw,
    field: str,
    value: object,
    user: User,
) -> object:
    """Validate and store one raw cell; returns the normalized journal value."""
    if field == "name":
        value_str = " ".join(str(value or "").split())
        if not value_str:
            message = "Укажите наименование сырья."
            raise KppkValidationError(message)
        rows = (await session.exec(select(KppkRaw).where(col(KppkRaw.id) != raw.id))).all()
        if any(row.name.lower() == value_str.lower() for row in rows):
            message = f"Сырьё «{value_str}» уже существует."
            raise KppkValidationError(message)
        raw.name = value_str
        stored: object = value_str
    elif field == "rawNum":
        number = _check_source_number(value)
        if number is not None:
            duplicate = (
                await session.exec(select(KppkRaw).where(col(KppkRaw.raw_num) == number, col(KppkRaw.id) != raw.id))
            ).first()
            if duplicate is not None:
                message = f"Номер {number} уже используется: «{duplicate.name}»."
                raise KppkValidationError(message)
        raw.raw_num = number
        stored = number
    else:
        message = f"Поле «{field}» нельзя изменить."
        raise KppkValidationError(message)
    raw.updated_at = _now()
    raw.updater_user_id = user.id
    session.add(raw)
    await session.flush()
    return stored


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def create_raw(
    session: AsyncSession,
    *,
    name: str,
    raw_num: int | None = None,
    user: User,
) -> tuple[KppkRawRowRead, list[AppliedCell]]:
    """Create a raw item with a case-insensitively unique name and number."""
    value = " ".join(str(name or "").split())
    if not value:
        message = "Укажите наименование сырья."
        raise KppkValidationError(message)
    rows = (await session.exec(select(KppkRaw))).all()
    if any(raw.name.lower() == value.lower() for raw in rows):
        message = f"Сырьё «{value}» уже существует."
        raise KppkValidationError(message)
    number = _check_source_number(raw_num)
    if number is not None:
        duplicate = (await session.exec(select(KppkRaw).where(col(KppkRaw.raw_num) == number))).first()
        if duplicate is not None:
            message = f"Номер {number} уже используется: «{duplicate.name}»."
            raise KppkValidationError(message)
    raw = KppkRaw(
        raw_num=number,
        name=value,
        created_at=_now(),
        updated_at=_now(),
        updater_user_id=user.id,
    )
    session.add(raw)
    await session.flush()
    row = KppkRawRowRead(
        id=raw.id,
        rawNum=raw.raw_num,
        name=raw.name,
        createdAt=raw.created_at,
        updatedAt=raw.updated_at,
        updatedBy=raw.updated_by,
    )
    applied = [
        AppliedCell(
            target_table=KppkRaw.__tablename__,
            row_id=raw.id,
            field="__created",
            old_value=None,
            new_value={"rawNum": row.rawNum, "name": row.name},
        )
    ]
    return row, applied


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_raw_cell(
    session: AsyncSession,
    *,
    raw_id: UUID,
    field: str,
    value: object,
    user: User,
) -> list[AppliedCell]:
    """Edit one cell of a raw row (name / rawNum)."""
    raw = await session.get(KppkRaw, raw_id)
    if raw is None:
        message = "Сырьё не найдено."
        raise KppkNotFoundError(message)
    old_value = _raw_field_value(raw, field)
    stored = await _apply_raw_cell(session, raw=raw, field=field, value=value, user=user)
    return [
        AppliedCell(
            target_table=KppkRaw.__tablename__,
            row_id=raw_id,
            field=field,
            old_value=old_value,
            new_value=stored,
        )
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def delete_raw(session: AsyncSession, *, raw_id: UUID) -> list[AppliedCell]:
    """Delete a raw item (no references exist in this phase)."""
    raw = await session.get(KppkRaw, raw_id)
    if raw is None:
        message = "Сырьё не найдено."
        raise KppkNotFoundError(message)
    snapshot: dict[str, object] = {"rawNum": raw.raw_num, "name": raw.name}
    await session.delete(raw)
    await session.flush()
    return [
        AppliedCell(
            target_table=KppkRaw.__tablename__,
            row_id=raw_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def read_org_rows(session: AsyncSession) -> list[KppkOrgRowRead]:
    """Rows of the «ДЗО» dictionary ordered by name."""
    orgs = (await session.exec(select(KppkOrg).order_by(col(KppkOrg.name)))).all()
    return [
        KppkOrgRowRead(
            id=org.id,
            code=org.code,
            name=org.name,
            createdAt=org.created_at,
            updatedAt=org.updated_at,
            updatedBy=org.updated_by,
        )
        for org in orgs
    ]


async def read_org_names(session: AsyncSession) -> list[str]:
    """Display names of all ДЗО ordered by name (the export dropdown list)."""
    names = (await session.exec(select(KppkOrg.name).order_by(col(KppkOrg.name)))).all()
    return list(names)


def _check_org_code(code: object) -> str:
    value = " ".join(str(code or "").split())
    if not value:
        message = "Укажите код ДЗО."
        raise KppkValidationError(message)
    if len(value) > 20:
        message = "Код ДЗО слишком длинный (до 20 символов)."
        raise KppkValidationError(message)
    return value


def _check_org_name(name: object) -> str:
    value = " ".join(str(name or "").split())
    if not value:
        message = "Укажите наименование ДЗО."
        raise KppkValidationError(message)
    if len(value) > 200:
        message = "Наименование ДЗО слишком длинное (до 200 символов)."
        raise KppkValidationError(message)
    return value


def _org_field_value(org: KppkOrg, field: str) -> object:
    """Return the current journal value of one ДЗО cell (the undo «old value»)."""
    if field == "code":
        return org.code
    if field == "name":
        return org.name
    message = f"Поле «{field}» нельзя изменить."
    raise KppkValidationError(message)


async def _apply_org_cell(
    session: AsyncSession,
    *,
    org: KppkOrg,
    field: str,
    value: object,
    user: User,
) -> object:
    """Validate and store one ДЗО cell; returns the normalized journal value."""
    if field == "code":
        code_value = _check_org_code(value)
        duplicate = (
            await session.exec(select(KppkOrg).where(col(KppkOrg.code) == code_value, col(KppkOrg.id) != org.id))
        ).first()
        if duplicate is not None:
            message = f"Код «{code_value}» уже используется."
            raise KppkValidationError(message)
        org.code = code_value
        stored: object = code_value
    elif field == "name":
        name_value = _check_org_name(value)
        duplicate = (
            await session.exec(
                select(KppkOrg).where(func.lower(col(KppkOrg.name)) == name_value.lower(), col(KppkOrg.id) != org.id)
            )
        ).first()
        if duplicate is not None:
            message = f"ДЗО «{name_value}» уже существует."
            raise KppkValidationError(message)
        org.name = name_value
        stored = name_value
    else:
        message = f"Поле «{field}» нельзя изменить."
        raise KppkValidationError(message)
    org.updated_at = _now()
    org.updater_user_id = user.id
    session.add(org)
    await session.flush()
    return stored


async def _check_org_deletable(session: AsyncSession, org: KppkOrg) -> None:
    """Shared deletion guard: no product links and no source-copy references."""
    links = (
        await session.exec(select(func.count()).select_from(KppkOrgProduct).where(col(KppkOrgProduct.org_id) == org.id))
    ).one()
    if links:
        message = "У этого ДЗО есть продуктовые позиции — сначала удалите их."
        raise KppkValidationError(message)
    if org.num is not None:
        message = "Это ДЗО из исходной выгрузки — на него ссылается копия данных факта/плана."
        raise KppkValidationError(message)


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def create_org(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    user: User,
) -> tuple[KppkOrgRowRead, list[AppliedCell]]:
    """Create a ДЗО with a unique code and name."""
    code_value = _check_org_code(code)
    name_value = _check_org_name(name)
    duplicate = (
        await session.exec(
            select(KppkOrg).where(
                (col(KppkOrg.code) == code_value) | (func.lower(col(KppkOrg.name)) == name_value.lower())
            )
        )
    ).first()
    if duplicate is not None:
        message = f"ДЗО «{name_value}» или код «{code_value}» уже существуют."
        raise KppkValidationError(message)
    org = KppkOrg(
        code=code_value,
        name=name_value,
        created_at=_now(),
        updated_at=_now(),
        updater_user_id=user.id,
    )
    session.add(org)
    await session.flush()
    row = KppkOrgRowRead(
        id=org.id,
        code=org.code,
        name=org.name,
        createdAt=org.created_at,
        updatedAt=org.updated_at,
        updatedBy=org.updated_by,
    )
    applied = [
        AppliedCell(
            target_table=KppkOrg.__tablename__,
            row_id=org.id,
            field="__created",
            old_value=None,
            new_value={"code": row.code, "name": row.name},
        )
    ]
    return row, applied


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_org_cell(
    session: AsyncSession,
    *,
    org_id: UUID,
    field: str,
    value: object,
    user: User,
) -> list[AppliedCell]:
    """Edit one cell of a ДЗО row (code / name)."""
    org = await session.get(KppkOrg, org_id)
    if org is None:
        message = "ДЗО не найдено."
        raise KppkNotFoundError(message)
    old_value = _org_field_value(org, field)
    stored = await _apply_org_cell(session, org=org, field=field, value=value, user=user)
    return [
        AppliedCell(
            target_table=KppkOrg.__tablename__,
            row_id=org_id,
            field=field,
            old_value=old_value,
            new_value=stored,
        )
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def delete_org(session: AsyncSession, *, org_id: UUID) -> list[AppliedCell]:
    """Delete a ДЗО that is not referenced by links or by the source copies."""
    org = await session.get(KppkOrg, org_id)
    if org is None:
        message = "ДЗО не найдено."
        raise KppkNotFoundError(message)
    await _check_org_deletable(session, org)
    snapshot: dict[str, object] = {"code": org.code, "name": org.name}
    await session.delete(org)
    await session.flush()
    return [
        AppliedCell(
            target_table=KppkOrg.__tablename__,
            row_id=org_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def _apply_product_record(
    session: AsyncSession,
    *,
    row_id: UUID,
    snapshot: object,
    user: User,
) -> None:
    """
    Undo/redo of a created/deleted product.

    A `None` snapshot removes the row (redo of a deletion or undo of a
    creation); a dict restores it under its original id (stale state — a row
    already present — is a no-op, the «Сырьё» semantics).
    """
    product = await session.get(KppkProduct, row_id)
    if snapshot is None:
        if product is None:
            return
        links = (await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == row_id))).all()
        for link in links:
            await session.delete(link)
        await session.flush()
        await session.delete(product)
        await session.flush()
        return
    if not isinstance(snapshot, dict):
        message = "Некорректные данные для восстановления позиции."
        raise KppkValidationError(message)
    if product is not None:
        return
    product_name = _check_product_name(snapshot.get("name"))
    if await _product_by_name(session, product_name) is not None:
        message = f"Продукт «{product_name}» уже существует."
        raise KppkValidationError(message)
    number = _check_source_number(snapshot.get("productNum"))
    if number is not None:
        duplicate = (await session.exec(select(KppkProduct).where(col(KppkProduct.product_num) == number))).first()
        if duplicate is not None:
            message = f"Номер {number} уже используется: «{duplicate.name}»."
            raise KppkValidationError(message)
    org_names = _check_org_names(snapshot.get("orgNames"))
    org_ids = await _org_ids_by_names(session, org_names)
    restored = KppkProduct(
        id=row_id,
        product_num=number,
        name=product_name,
        created_at=_now(),
        updated_at=_now(),
        updater_user_id=user.id,
    )
    session.add(restored)
    await session.flush()
    await replace_org_links(session, row_id, org_ids, updater_user_id=user.id)


async def _apply_raw_record(
    session: AsyncSession,
    *,
    row_id: UUID,
    snapshot: object,
    user: User,
) -> None:
    """Undo/redo of a created/deleted raw item (see `_apply_product_record`)."""
    raw = await session.get(KppkRaw, row_id)
    if snapshot is None:
        if raw is None:
            return
        await session.delete(raw)
        await session.flush()
        return
    if not isinstance(snapshot, dict):
        message = "Некорректные данные для восстановления сырья."
        raise KppkValidationError(message)
    if raw is not None:
        return
    name = " ".join(str(snapshot.get("name") or "").split())
    if not name:
        message = "Укажите наименование сырья."
        raise KppkValidationError(message)
    rows = (await session.exec(select(KppkRaw))).all()
    if any(row.name.lower() == name.lower() for row in rows):
        message = f"Сырьё «{name}» уже существует."
        raise KppkValidationError(message)
    number = _check_source_number(snapshot.get("rawNum"))
    if number is not None:
        duplicate = (await session.exec(select(KppkRaw).where(col(KppkRaw.raw_num) == number))).first()
        if duplicate is not None:
            message = f"Номер {number} уже используется: «{duplicate.name}»."
            raise KppkValidationError(message)
    restored = KppkRaw(
        id=row_id,
        raw_num=number,
        name=name,
        created_at=_now(),
        updated_at=_now(),
        updater_user_id=user.id,
    )
    session.add(restored)
    await session.flush()


async def _apply_org_record(
    session: AsyncSession,
    *,
    row_id: UUID,
    snapshot: object,
    user: User,
) -> None:
    """Undo/redo of a created/deleted ДЗО (see `_apply_product_record`)."""
    org = await session.get(KppkOrg, row_id)
    if snapshot is None:
        if org is None:
            return
        # The interactive deletion guard applies to the replayed deletion too:
        # a product linked to the org in the meantime makes the action skip.
        await _check_org_deletable(session, org)
        await session.delete(org)
        await session.flush()
        return
    if not isinstance(snapshot, dict):
        message = "Некорректные данные для восстановления ДЗО."
        raise KppkValidationError(message)
    if org is not None:
        return
    code_value = _check_org_code(snapshot.get("code"))
    name_value = _check_org_name(snapshot.get("name"))
    duplicate = (
        await session.exec(
            select(KppkOrg).where(
                (col(KppkOrg.code) == code_value) | (func.lower(col(KppkOrg.name)) == name_value.lower())
            )
        )
    ).first()
    if duplicate is not None:
        message = f"ДЗО «{name_value}» или код «{code_value}» уже существуют."
        raise KppkValidationError(message)
    restored = KppkOrg(
        id=row_id,
        code=code_value,
        name=name_value,
        created_at=_now(),
        updated_at=_now(),
        updater_user_id=user.id,
    )
    session.add(restored)
    await session.flush()


async def apply_kppk_cell_value(
    session: AsyncSession,
    *,
    target_table: str,
    row_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """
    Apply a journaled cell value of a «КППК» dictionary row (undo/redo).

    Cell edits go through the same validators as the interactive PATCH; a
    `__created`/`__deleted` field is a whole-row snapshot: `None` removes the
    row, a dict restores it under its original id.
    """
    if target_table == KppkProduct.__tablename__:
        if field in {"__created", "__deleted"}:
            await _apply_product_record(session, row_id=row_id, snapshot=value, user=user)
            return
        product = await session.get(KppkProduct, row_id)
        if product is None:
            message = "Позиция не найдена."
            raise KppkNotFoundError(message)
        await _apply_product_cell(session, product=product, field=field, value=value, user=user)
        return
    if target_table == KppkRaw.__tablename__:
        if field in {"__created", "__deleted"}:
            await _apply_raw_record(session, row_id=row_id, snapshot=value, user=user)
            return
        raw = await session.get(KppkRaw, row_id)
        if raw is None:
            message = "Сырьё не найдено."
            raise KppkNotFoundError(message)
        await _apply_raw_cell(session, raw=raw, field=field, value=value, user=user)
        return
    if target_table == KppkOrg.__tablename__:
        if field in {"__created", "__deleted"}:
            await _apply_org_record(session, row_id=row_id, snapshot=value, user=user)
            return
        org = await session.get(KppkOrg, row_id)
        if org is None:
            message = "ДЗО не найдено."
            raise KppkNotFoundError(message)
        await _apply_org_cell(session, org=org, field=field, value=value, user=user)
        return
    if target_table in {KppkRealization.__tablename__, KppkRealizationPlan.__tablename__}:
        await apply_kppk_realization_cell_value(
            session,
            target_table=target_table,
            row_id=row_id,
            field=field,
            value=value,
        )
        return
    if target_table == KppkCost.__tablename__:
        await update_cost_cell(session, row_id=row_id, field=field, value=value)
        return
    message = f"Неизвестная таблица «{target_table}»."
    raise KppkValidationError(message)
