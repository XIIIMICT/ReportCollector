"""
Read/write service of the «КППК» project.

This phase exposes the normalized source copies as admin dictionaries: the
«Продуктовые позиции» (a product with its ДЗО set, Матрица продуктов.xlsx),
the raw list («Сырьевые позиции»), «ДЗО» and «Периоды». Access follows the
other projects: the role comes from the shared `user_roles` table; any project
member may read, dictionary mutations require the project admin.

Every dictionary is editable, but the seeder re-copies everything from
`raw.kppk_*` on the next run — a deleted source row returns unless removed
from the source as well. The source number («№») of products and raws is
editable like any other cell and stays unique — an occupied number is
rejected with a clear message. Rows referenced by the fact/plan copies (ДЗО,
periods) or by the matrix links (products, ДЗО) refuse deletion with a RESTRICT.

The dictionary mutations return the applied cells for the shared undo/redo
journal (the «Сырьё»/«Ремонт» mechanism): one cell edit, a creation
(«__created») or a deletion («__deleted») is one action each, and
`apply_kppk_cell_value` replays them through the same validators.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.service import CurrentUserIdentity, get_user_by_identity, sync_project_memberships
from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.config import get_settings
from src.kppk.constants import KPPK_PROJECT_KEY
from src.kppk.models import (
    KppkOrg,
    KppkOrgProduct,
    KppkPeriod,
    KppkProduct,
    KppkRaw,
    KppkRealization,
    KppkRealizationPlan,
)

from .schemas import (
    KppkOrgRowRead,
    KppkPeriodRowRead,
    KppkProductPositionCreateRequest,
    KppkProductPositionRowRead,
    KppkRawRowRead,
    KppkRealizationRowRead,
    KppkUserCreateRequest,
    KppkUserRowRead,
    KppkUserUpdateRequest,
)


class NoKppkAccessError(Exception):
    """The user has no access to the «КППК» project."""


class KppkNotFoundError(ValueError):
    """The requested row does not exist."""


class KppkValidationError(ValueError):
    """The edit violates a uniqueness rule or a reference (RESTRICT)."""


def _audit_name(user: User) -> str:
    return user.name or user.email or ""


def _now() -> datetime:
    return datetime.now(UTC)


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


# ─── Access ────────────────────────────────────────────────────────────────


async def resolve_kppk_access(session: AsyncSession, identity: CurrentUserIdentity) -> tuple[User, list[ProjectRole]]:
    """Return (user, active roles of the kppk project); raises without access."""
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise NoKppkAccessError
    roles = list(
        (
            await session.exec(
                select(col(UserRole.role))
                .join(Project, col(Project.id) == col(UserRole.project_id))
                .where(
                    col(UserRole.user_id) == user.id,
                    col(Project.key) == KPPK_PROJECT_KEY,
                    col(UserRole.is_active).is_(True),
                )
            )
        ).all()
    )
    if not roles:
        raise NoKppkAccessError
    return user, roles


def is_kppk_admin(roles: list[ProjectRole]) -> bool:
    return ProjectRole.ADMIN in roles


# ─── «Продуктовые позиции» (a product with its ДЗО set) ────────────────────


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
    updated_by: str,
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
                    updated_by=updated_by,
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
        await replace_org_links(session, product.id, org_ids, updated_by=_audit_name(user))
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
    product.updated_by = _audit_name(user)
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
        updated_by=_audit_name(user),
    )
    session.add(product)
    await session.flush()
    await replace_org_links(session, product.id, org_ids, updated_by=_audit_name(user))
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


# ─── «Сырьевые позиции» (raw list) ────────────────────────────────────────


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
    raw.updated_by = _audit_name(user)
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
        updated_by=_audit_name(user),
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


# ─── «ДЗО» ────────────────────────────────────────────────────────────────


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
    org.updated_by = _audit_name(user)
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
    if org.org_id is not None:
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
        updated_by=_audit_name(user),
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


# ─── Undo/redo application ────────────────────────────────────────────────


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
        updated_by=_audit_name(user),
    )
    session.add(restored)
    await session.flush()
    await replace_org_links(session, row_id, org_ids, updated_by=_audit_name(user))


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
        updated_by=_audit_name(user),
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
        updated_by=_audit_name(user),
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
    message = f"Неизвестная таблица «{target_table}»."
    raise KppkValidationError(message)


# ─── «Периоды» ────────────────────────────────────────────────────────────


async def read_period_rows(session: AsyncSession) -> list[KppkPeriodRowRead]:
    """Rows of the «Периоды» dictionary ordered chronologically."""
    periods = (await session.exec(select(KppkPeriod).order_by(col(KppkPeriod.first_date)))).all()
    return [
        KppkPeriodRowRead(
            id=period.id,
            monthName=period.month_name,
            yearNum=period.year_num,
            firstDate=period.first_date,
            lastDate=period.last_date,
            createdAt=period.created_at,
            updatedAt=period.updated_at,
            updatedBy=period.updated_by,
        )
        for period in periods
    ]


def _check_period_dates(first_date: date, last_date: date) -> None:
    if last_date < first_date:
        message = "Дата окончания периода не может быть раньше начала."
        raise KppkValidationError(message)


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def create_period(
    session: AsyncSession,
    *,
    month_name: str,
    year_num: int,
    first_date: date,
    last_date: date,
    user: User,
) -> KppkPeriodRowRead:
    """Create a report month."""
    month_value = " ".join(str(month_name or "").split())
    if not month_value:
        message = "Укажите месяц."
        raise KppkValidationError(message)
    _check_period_dates(first_date, last_date)
    duplicate = (
        await session.exec(
            select(KppkPeriod).where(
                func.lower(col(KppkPeriod.month_name)) == month_value.lower(),
                col(KppkPeriod.year_num) == year_num,
            )
        )
    ).first()
    if duplicate is not None:
        message = f"Период «{month_value} {year_num}» уже существует."
        raise KppkValidationError(message)
    period = KppkPeriod(
        month_name=month_value,
        year_num=year_num,
        first_date=first_date,
        last_date=last_date,
        created_at=_now(),
        updated_at=_now(),
        updated_by=_audit_name(user),
    )
    session.add(period)
    await session.flush()
    return KppkPeriodRowRead(
        id=period.id,
        monthName=period.month_name,
        yearNum=period.year_num,
        firstDate=period.first_date,
        lastDate=period.last_date,
        createdAt=period.created_at,
        updatedAt=period.updated_at,
        updatedBy=period.updated_by,
    )


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_period_cell(
    session: AsyncSession,
    *,
    period_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """Edit one cell of a period row (monthName / yearNum / firstDate / lastDate)."""
    period = await session.get(KppkPeriod, period_id)
    if period is None:
        message = "Период не найден."
        raise KppkNotFoundError(message)
    if field == "monthName":
        month_value = " ".join(str(value or "").split())
        if not month_value:
            message = "Укажите месяц."
            raise KppkValidationError(message)
        duplicate = (
            await session.exec(
                select(KppkPeriod).where(
                    func.lower(col(KppkPeriod.month_name)) == month_value.lower(),
                    col(KppkPeriod.year_num) == period.year_num,
                    col(KppkPeriod.id) != period_id,
                )
            )
        ).first()
        if duplicate is not None:
            message = f"Период «{month_value} {period.year_num}» уже существует."
            raise KppkValidationError(message)
        period.month_name = month_value
    elif field == "yearNum":
        try:
            year_value = int(str(value).strip())
        except (TypeError, ValueError) as error:
            message = "Год — целое число."
            raise KppkValidationError(message) from error
        duplicate = (
            await session.exec(
                select(KppkPeriod).where(
                    func.lower(col(KppkPeriod.month_name)) == period.month_name.lower(),
                    col(KppkPeriod.year_num) == year_value,
                    col(KppkPeriod.id) != period_id,
                )
            )
        ).first()
        if duplicate is not None:
            message = f"Период «{period.month_name} {year_value}» уже существует."
            raise KppkValidationError(message)
        period.year_num = year_value
    elif field in {"firstDate", "lastDate"}:
        raw_date = str(value or "").strip()
        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError as error:
            message = f"Некорректная дата «{raw_date}»."
            raise KppkValidationError(message) from error
        if field == "firstDate":
            _check_period_dates(parsed, period.last_date)
            period.first_date = parsed
        else:
            _check_period_dates(period.first_date, parsed)
            period.last_date = parsed
    else:
        message = f"Поле «{field}» нельзя изменить."
        raise KppkValidationError(message)
    period.updated_at = _now()
    period.updated_by = _audit_name(user)
    session.add(period)
    await session.flush()


# ─── «Реализация» (Таблицы.xlsx sheet «Реализация») ───────────────────────


REALIZATION_SLICE_FIELDS: dict[str, tuple[str, ...]] = {
    "bp": ("buyoutBpPlan", "fcaPriceBpPlan", "monthBpPlan", "fcaPriceMarketBpPlan"),
    "op": ("buyoutMaxPlan", "fcaPriceMaxPlan", "monthMaxPlan", "fcaPriceMarketMaxPlan"),
    "fact": ("buyoutFact", "fcaPriceFact", "fact", "fcaPriceMarketFact"),
}

_REALIZATION_FIELD_ATTRIBUTES: dict[str, str] = {
    "buyoutBpPlan": "buyout_bp_plan",
    "fcaPriceBpPlan": "fca_price_bp_plan",
    "monthBpPlan": "month_bp_plan",
    "fcaPriceMarketBpPlan": "fca_price_market_bp_plan",
    "buyoutMaxPlan": "buyout_max_plan",
    "fcaPriceMaxPlan": "fca_price_max_plan",
    "monthMaxPlan": "month_max_plan",
    "fcaPriceMarketMaxPlan": "fca_price_market_max_plan",
    "buyoutFact": "buyout_fact",
    "fcaPriceFact": "fca_price_fact",
    "fact": "fact",
    "fcaPriceMarketFact": "fca_price_market_fact",
}


# The «Реализация» view works on one fixed period until the period filter
# appears: the newest period by first_date that has plan data (the form is
# filled month by month, the fact copy lands the current month last).
def _realization_period_from_rows(
    periods: Sequence[KppkPeriod],
    plan_period_ids: set[int],
) -> KppkPeriod:
    """Pick the newest period among those referenced, else the newest row."""
    referenced = [period for period in periods if period.period_id is not None and period.period_id in plan_period_ids]
    pool = referenced or periods
    return max(pool, key=lambda period: (period.first_date, period.period_id or 0))


async def read_current_period(
    session: AsyncSession, *, month: int | None = None, year: int | None = None
) -> KppkPeriod:
    """
    Return the period the «Реализация» form works on.

    With `month`+`year` (both required, 1-based) — the named calendar period;
    otherwise the newest period the form data references (the default until
    the user picks an explicit month/year in the toolbar).
    """
    periods = (await session.exec(select(KppkPeriod))).all()
    if not periods:
        message = "Периоды не заведены — заведите справочник «Периоды»."
        raise KppkValidationError(message)
    if month is not None and year is not None:
        named = [
            period
            for period in periods
            if period.first_date is not None and period.first_date.year == year and period.first_date.month == month
        ]
        if not named:
            message = f"Период {month:02d}.{year} в справочнике «Периоды» не найден."
            raise KppkValidationError(message)
        return max(named, key=lambda period: period.period_id or 0)
    plan_period_ids = {
        row for row in (await session.exec(select(col(KppkRealizationPlan.period_id)).distinct())).all() if row
    }
    return _realization_period_from_rows(list(periods), plan_period_ids)


async def read_kppk_user_rows(session: AsyncSession) -> list[KppkUserRowRead]:
    """Return the «КППК» project members (the users with a link to the project)."""
    project_id = await kppk_project_id(session)
    rows = (
        await session.exec(
            select(User, UserRole)
            .join(UserRole, col(UserRole.user_id) == col(User.id))
            .where(col(UserRole.project_id) == project_id, col(User.is_deleted).is_(False))
            .order_by(func.coalesce(User.name, User.email))
        )
    ).all()
    return [
        KppkUserRowRead(
            id=user.id,
            email=str(user.email or ""),
            fullname=user.name or "",
            isAdmin=link.role == ProjectRole.ADMIN,
            isActive=link.is_active,
            lastAuthenticatedAt=user.last_authenticated_at,
            createdAt=user.created_at,
            updatedAt=link.updated_at,
            updatedBy=link.updated_by,
        )
        for user, link in rows
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def create_kppk_user(
    session: AsyncSession,
    *,
    request: KppkUserCreateRequest,
    editor: User,
) -> tuple[User, list[AppliedCell]]:
    """Provision a project administrator without touching raw-material asset grants."""
    if not request.isAdmin:
        msg = "В КППК пока доступна только роль администратора."
        raise KppkValidationError(msg)
    project_id = await kppk_project_id(session)
    person = (await session.exec(select(User).where(func.lower(col(User.email)) == str(request.email)))).one_or_none()
    if person is not None and await session.get(UserRole, (person.id, project_id)) is not None:
        msg = "Пользователь уже добавлен в проект."
        raise KppkValidationError(msg)
    if person is None:
        person = User(email=request.email, name=request.fullname.strip())
        session.add(person)
        await session.flush()
    person.is_deleted = False
    session.add(person)
    link = UserRole(
        user_id=person.id, project_id=project_id, role=ProjectRole.ADMIN, is_active=True, updated_by=_audit_name(editor)
    )
    session.add(link)
    await sync_project_memberships(session, person.id)
    await session.flush()
    snapshot = {
        "email": str(person.email or ""),
        "fullname": person.name or "",
        "role": link.role.value,
        "isActive": link.is_active,
    }
    return person, [
        AppliedCell(
            target_table=User.__tablename__, row_id=person.id, field="__created", old_value=None, new_value=snapshot
        )
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_kppk_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    request: KppkUserUpdateRequest,
    editor: User,
) -> list[AppliedCell]:
    if user_id == editor.id:
        msg = "Нельзя редактировать собственную запись в справочнике пользователей."
        raise KppkValidationError(msg)
    project_id = await kppk_project_id(session)
    person = await session.get(User, user_id)
    link = await session.get(UserRole, (user_id, project_id))
    if person is None or person.is_deleted or link is None:
        msg = "Пользователь не найден."
        raise KppkNotFoundError(msg)
    values = request.model_dump(exclude_unset=True)
    if get_settings().keycloak_enabled and any(
        key in values and values[key] != old for key, old in (("email", person.email), ("fullname", person.name))
    ):
        msg = "Email и ФИО управляются корпоративной учётной записью."
        raise KppkValidationError(msg)
    if request.email is not None:
        duplicate = (
            await session.exec(
                select(User).where(func.lower(col(User.email)) == str(request.email), col(User.id) != user_id)
            )
        ).first()
        if duplicate is not None:
            msg = "Пользователь с таким email уже существует."
            raise KppkValidationError(msg)
    applied: list[AppliedCell] = []
    for field, model, attribute, value in (
        ("email", person, "email", request.email),
        ("fullname", person, "name", request.fullname.strip() if request.fullname is not None else None),
        (
            "isAdmin",
            link,
            "role",
            None if request.isAdmin is None else ProjectRole.ADMIN if request.isAdmin else ProjectRole.ASSET_WORKER,
        ),
        ("isActive", link, "is_active", request.isActive),
    ):
        if value is None:
            continue
        old = getattr(model, attribute)
        if old == value:
            continue
        setattr(model, attribute, value)
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user_id,
                field=field,
                old_value=(old == ProjectRole.ADMIN) if field == "isAdmin" else old,
                new_value=(value == ProjectRole.ADMIN) if field == "isAdmin" else value,
            )
        )
    if request.resetPassword:
        if get_settings().keycloak_enabled:
            msg = "Пароль управляется корпоративной учётной записью."
            raise KppkValidationError(msg)
        person.password_hash = None
    person.updated_at = _now()
    link.updated_at = _now()
    link.updated_by = _audit_name(editor)
    session.add(person)
    session.add(link)
    await session.flush()
    return applied


async def apply_kppk_user_value(
    session: AsyncSession,
    *,
    row_id: UUID,
    field: str,
    value: object,
    editor: User,
) -> None:
    """Replay only the KPPK membership; never dispatch shared users to the raw project."""
    if field not in {"__created", "__deleted"}:
        await update_kppk_user(
            session, user_id=row_id, request=KppkUserUpdateRequest.model_validate({field: value}), editor=editor
        )
        return
    if value is None:
        if field == "__deleted":
            project_id = await kppk_project_id(session)
            link = await session.get(UserRole, (row_id, project_id))
            if row_id == editor.id or (link is not None and link.is_active):
                msg = "Сначала снимите доступ пользователя к проекту; удалить себя нельзя."
                raise KppkValidationError(msg)
        await delete_kppk_project_user(session, user_id=row_id)
        return
    if not isinstance(value, dict):
        msg = "Некорректное действие над пользователем."
        raise KppkValidationError(msg)
    person = await session.get(User, row_id)
    if person is None:
        msg = "Пользователь не найден."
        raise KppkNotFoundError(msg)
    project_id = await kppk_project_id(session)
    person.is_deleted = False
    session.add(person)
    link = await session.get(UserRole, (row_id, project_id))
    if link is None:
        link = UserRole(user_id=row_id, project_id=project_id, role=ProjectRole.ADMIN)
    link.role = ProjectRole(str(value["role"]))
    link.is_active = bool(value["isActive"])
    link.updated_by = _audit_name(editor)
    link.updated_at = _now()
    session.add(link)
    await session.flush()


async def delete_kppk_project_user(session: AsyncSession, *, user_id: UUID) -> None:
    """
    Remove a user from «КППК» (the КППК subscription is its `UserRole` link).

    The account is soft-deleted once no project link is left, so the person
    disappears from the dictionaries instead of lingering as an empty row.
    """
    project_id = await kppk_project_id(session)
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        message = "Пользователь не найден."
        raise KppkNotFoundError(message)
    link = await session.get(UserRole, (user_id, project_id))
    if link is None:
        message = "Пользователь не найден."
        raise KppkNotFoundError(message)
    await session.delete(link)
    await session.flush()
    remaining = (await session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))).first()
    if remaining is None:
        user.is_deleted = True
        user.updated_at = datetime.now(UTC)
        session.add(user)
        await session.flush()


async def kppk_project_id(session: AsyncSession) -> UUID:
    project_id = (await session.exec(select(col(Project.id)).where(col(Project.key) == KPPK_PROJECT_KEY))).one_or_none()
    if project_id is None:
        message = "Проект «КППК» не найден."
        raise KppkNotFoundError(message)
    return project_id


async def read_market_codes(session: AsyncSession) -> list[str]:
    """
    Return the рынок codes («Рынок сбыта», distinct `code` of dict.markets).

    The markets dictionary is prod-owned (`dict.markets`); the seeder creates
    it only when missing, so the test database can lack the schema — the codes
    of the data copies are the fallback then (the same РФ/БЗ/ДЗ vocabulary,
    per the template).
    """
    connection = await session.connection()
    has_markets = (
        await connection.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'dict' AND table_name = 'markets'")
        )
    ).first() is not None
    if has_markets:
        # The insertion order is a stable display order; DISTINCT by code keeps
        # the «Экспорт ТС»/«Экспорт СНГ» names behind their shared БЗ code.
        rows = (
            await connection.execute(
                text(
                    # UUID has no min() — the key casts to text. It's only the
                    # stable display order (РФ, БЗ, ДЗ by first insertion).
                    "SELECT code FROM dict.markets GROUP BY code ORDER BY min(created_at), min(id::text)"
                )
            )
        ).all()
        if rows:
            return [row[0] for row in rows]
    # Without dict.markets (the disposable test databases): the distinct codes
    # of the data copies themselves (identical vocabulary). The schema name is
    # a seeder-constant setting, not user input.
    schema = get_settings().postgres_schema
    # `kppk_realization` / `kppk_realization_plan` are seeder-owned constant
    # names and `schema` is a server setting — no user input reaches this SQL.
    region_query: str = (
        f'SELECT region FROM "{schema}"."kppk_realization" UNION SELECT region FROM "{schema}"."kppk_realization_plan"'  # noqa: S608 — constant table names, no user input
    )
    regions = (await connection.execute(text(region_query))).all()
    return sorted({row[0] for row in regions if row[0]})


def _plan_realization_row_read(row: KppkRealizationPlan) -> KppkRealizationRowRead:
    """Build the API shape of a plan-copy row (the two plan slices' columns)."""
    return KppkRealizationRowRead(
        id=row.id,
        product=row.product,
        region=row.region,
        buyoutBpPlan=row.buyout_bp_plan,
        fcaPriceBpPlan=row.fca_price_bp_plan,
        monthBpPlan=row.month_bp_plan,
        fcaPriceMarketBpPlan=row.fca_price_market_bp_plan,
        buyoutMaxPlan=row.buyout_max_plan,
        fcaPriceMaxPlan=row.fca_price_max_plan,
        monthMaxPlan=row.month_max_plan,
        fcaPriceMarketMaxPlan=row.fca_price_market_max_plan,
    )


def _fact_realization_row_read(row: KppkRealization) -> KppkRealizationRowRead:
    """Build the API shape of a fact-copy row (the fact slice's columns)."""
    return KppkRealizationRowRead(
        id=row.id,
        product=row.product,
        region=row.region,
        buyoutFact=row.buyout_fact,
        fcaPriceFact=row.fca_price_fact,
        fact=row.fact,
        fcaPriceMarketFact=row.fca_price_market_fact,
    )


async def read_realization_rows(
    session: AsyncSession,
    *,
    slice_id: str,
    org_id: UUID,
) -> list[KppkRealizationRowRead]:
    """List the rows of one «Реализация» slice (period × ДЗО, product/region ordered)."""
    fields = REALIZATION_SLICE_FIELDS.get(slice_id)
    if fields is None:
        message = "Неизвестный срез «Реализации»."
        raise KppkValidationError(message)
    org = await session.get(KppkOrg, org_id)
    if org is None:
        message = "ДЗО не найдено."
        raise KppkNotFoundError(message)
    if org.org_id is None:
        # A hand-added ДЗО without a source id holds no copies' rows.
        return []
    period = await read_current_period(session)
    markets = await read_market_codes(session)
    region_order = {code: index for index, code in enumerate(markets)}

    # The Заказчик задал порядок рынка как в справочнике markets (РФ, БЗ, ДЗ);
    # внутри продукта строки группируются по нему.
    def region_key(row: KppkRealization | KppkRealizationPlan) -> tuple[str, int]:
        return (row.product, region_order.get(row.region, len(region_order)))

    if slice_id == "fact":
        fact_rows = list(
            (
                await session.exec(
                    select(KppkRealization)
                    .where(
                        col(KppkRealization.org_id) == org.org_id,
                        col(KppkRealization.period_id) == period.period_id,
                        col(KppkRealization.rep_date) == period.last_date,
                    )
                    .order_by(col(KppkRealization.product))
                )
            ).all()
        )
        fact_rows.sort(key=region_key)
        return [_fact_realization_row_read(row) for row in fact_rows]
    plan_rows = list(
        (
            await session.exec(
                select(KppkRealizationPlan)
                .where(
                    col(KppkRealizationPlan.org_id) == org.org_id,
                    col(KppkRealizationPlan.period_id) == period.period_id,
                    col(KppkRealizationPlan.rep_date) == period.last_date,
                )
                .order_by(col(KppkRealizationPlan.product))
            )
        ).all()
    )
    plan_rows.sort(key=region_key)
    return [_plan_realization_row_read(row) for row in plan_rows]


async def read_realization_row_by_id(
    session: AsyncSession,
    *,
    slice_id: str,
    row_id: UUID,
) -> KppkRealizationRowRead | None:
    """Read one realization row after a PATCH (slice decides the copy table)."""
    if slice_id == "fact":
        row = await session.get(KppkRealization, row_id)
        return None if row is None else _fact_realization_row_read(row)
    if slice_id in {"bp", "op"}:
        row = await session.get(KppkRealizationPlan, row_id)
        return None if row is None else _plan_realization_row_read(row)
    message = "Неизвестный срез «Реализации»."
    raise KppkValidationError(message)


def _check_realization_decimal(value: object) -> Decimal | None:
    """Parse a numeric cell of «Реализации»; empty clears it (NULL)."""
    if value is None or (isinstance(value, str) and not str(value).strip()):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as error:
        message = "Значение должно быть числом."
        raise KppkValidationError(message) from error
    if not number.is_finite():
        message = "Значение должно быть числом."
        raise KppkValidationError(message)
    return number


async def _apply_realization_cell(
    session: AsyncSession,
    *,
    slice_id: str,
    row_id: UUID,
    field: str,
    value: object,
) -> tuple[Decimal | None, Decimal | None]:
    """Validate one numeric cell and store it; returns the (old, new) journal values."""
    fields = REALIZATION_SLICE_FIELDS.get(slice_id)
    if fields is None or field not in fields:
        message = f"Поле «{field}» нельзя изменить на этом срезе."
        raise KppkValidationError(message)
    attribute = _REALIZATION_FIELD_ATTRIBUTES[field]
    model = KppkRealization if slice_id == "fact" else KppkRealizationPlan
    row = await session.get(model, row_id)
    if row is None:
        message = "Строка не найдена — обновите вид (период или ДЗО изменились)."
        raise KppkNotFoundError(message)
    old_value = getattr(row, attribute)
    stored = _check_realization_decimal(value)
    setattr(row, attribute, stored)
    await session.flush()
    return old_value, stored


@project_write(KPPK_PROJECT_KEY, exclusive=False)
async def update_realization_cell(
    session: AsyncSession,
    *,
    slice_id: str,
    row_id: UUID,
    field: str,
    value: object,
    user: User,
) -> tuple[Decimal | None, Decimal | None]:
    """
    Edit one numeric cell of a «Реализация» row under the shared project lock.

    The slice names the table and the editable fields; the returned pair is
    the journal-replayable (old, new) values — the grid records carry the
    same camelCase DB attributes, so the redo applies the same vocabulary.
    """
    del user  # the realization copies keep no audit stamps (the seeder re-copies)
    return await _apply_realization_cell(session, slice_id=slice_id, row_id=row_id, field=field, value=value)


async def apply_kppk_realization_cell_value(
    session: AsyncSession,
    *,
    target_table: str,
    row_id: UUID,
    field: str,
    value: object,
) -> None:
    """Replay a journaled «Реализация» cell value (undo/redo), no audit stamp."""
    if target_table == KppkRealization.__tablename__:
        slice_id = "fact"
    elif target_table == KppkRealizationPlan.__tablename__:
        slice_id = "bp" if field in REALIZATION_SLICE_FIELDS["bp"] else "op"
    else:
        message = f"Неизвестная таблица реализации «{target_table}»."
        raise KppkValidationError(message)
    await _apply_realization_cell(session, slice_id=slice_id, row_id=row_id, field=field, value=value)


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def delete_period(session: AsyncSession, *, period_id: UUID) -> None:
    """Delete a period that is not referenced by the fact/plan copies."""
    period = await session.get(KppkPeriod, period_id)
    if period is None:
        message = "Период не найден."
        raise KppkNotFoundError(message)
    if period.period_id is not None:
        message = "Этот период из исходной выгрузки — на него ссылается копия данных факта/плана."
        raise KppkValidationError(message)
    await session.delete(period)
    await session.flush()


# NOTE: «Реализация» (Таблицы.xlsx) endpoints live in `realization_router.py`;
# cell edits replay through `apply_kppk_realization_cell_value` from the shared
# undo/redo stack (`src.collab.undo`).
