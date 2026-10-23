"""KPPK source dictionary reads, mutations and undo replay."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.kppk.constants import KPPK_PROJECT_KEY, normalize_text
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


async def read_product_position_rows(
    session: AsyncSession, *, grouped: bool = False
) -> list[KppkProductPositionRowRead]:
    """
    Rows of «Продуктовые позиции», in one of the tab's two shapes.

    The dictionary is stored as one row per product × ДЗО POSITION, and both
    presentation modes read from it:

    - `grouped=False` (the «Настройка акциза» mode): one row per POSITION, so
      the «Заполняемый акциз» flag can be set for each product at each asset;
      a product without any position (created by hand, or its last position
      deleted) is not shown here — it has no asset to name;
    - `grouped=True` (the default, the historical view): one row per PRODUCT
      with its whole ДЗО set in `orgNames`, which is how the tab was edited
      before the excise existed — a product is typed once, then the assets are
      multi-selected.

    Both orders follow the source number («№», unnumbered last), the name and,
    inside a product, the asset name.
    """
    query = (
        select(KppkProduct, KppkOrgProduct, KppkOrg)
        .outerjoin(KppkOrgProduct, col(KppkOrgProduct.product_id) == col(KppkProduct.id))
        .outerjoin(KppkOrg, col(KppkOrg.id) == col(KppkOrgProduct.org_id))
        .order_by(col(KppkProduct.product_num), col(KppkProduct.name), col(KppkOrg.name))
    )
    rows = (await session.exec(query)).all()
    if grouped:
        grouped_rows: dict[UUID, tuple[KppkProduct, list[str], list[str]]] = {}
        for product, _link, org in rows:
            entry = grouped_rows.setdefault(product.id, (product, [], []))
            if org is not None:
                # `orgNames` carries the FULL names (the multi-select edits by
                # them), `orgShortNames` the short ones the grid lists inline.
                entry[1].append(org.name)
                entry[2].append(org.code)
        return [
            KppkProductPositionRowRead(
                id=product.id,
                productId=product.id,
                productNum=product.product_num,
                name=product.name,
                orgName="",
                orgNames=org_names,
                orgShortNames=short_names,
                createdAt=product.created_at,
                updatedAt=product.updated_at,
                updatedBy=product.updated_by,
            )
            for product, org_names, short_names in (entry for _, entry in grouped_rows.items())
        ]
    return [
        KppkProductPositionRowRead(
            id=link.id,
            productId=product.id,
            productNum=product.product_num,
            name=product.name,
            orgName=org.name,
            orgCode=org.code,
            hasExcise=link.has_excise,
            createdAt=product.created_at,
            updatedAt=product.updated_at,
            updatedBy=product.updated_by,
        )
        for product, link, org in rows
        if link is not None and org is not None
    ]


def _check_product_name(name: object) -> str:
    value = normalize_text(str(name or ""))
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
    # Deduplicated, first occurrence wins: a multi-select cannot normally repeat
    # an asset, but a paste can — and a repeat must not hit the (org, product)
    # unique key as a second insert. Normalised like every stored text, so a
    # user-typed «АО  "Х"» still matches the dictionary row.
    names: list[str] = []
    for item in value:
        name = normalize_text(item)
        if name and name not in names:
            names.append(name)
    return names


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


async def _position_field_value(
    session: AsyncSession,
    *,
    product: KppkProduct,
    link: KppkOrgProduct | None,
    field: str,
) -> object:
    """Return the current journal value of one position cell (the undo «old value»)."""
    if field == "name":
        return product.name
    if field == "productNum":
        return product.product_num
    if field == "orgNames":
        return await _product_org_names(session, product.id)
    if link is None:
        message = "Позиция не найдена."
        raise KppkNotFoundError(message)
    if field == "hasExcise":
        return link.has_excise
    if field == "orgName":
        org = await session.get(KppkOrg, link.org_id)
        return org.name if org is not None else None
    message = f"Поле «{field}» нельзя изменить."
    raise KppkValidationError(message)


async def _apply_position_cell(
    session: AsyncSession,
    *,
    product: KppkProduct,
    link: KppkOrgProduct | None,
    field: str,
    value: object,
    user: User,
) -> object:
    """
    Validate and store one cell of a position; returns the normalized value.

    «№» and «Наименование» are PRODUCT cells (every position of the product
    shows them, and editing either repaints the product's whole block);
    «ДЗО» and «Заполняемый акциз» belong to THIS position.
    """
    if field == "name":
        product_name = _check_product_name(value)
        rows = (await session.exec(select(KppkProduct).where(col(KppkProduct.id) != product.id))).all()
        if any(row.name.lower() == product_name.lower() for row in rows):
            message = f"Продукт «{product_name}» уже существует."
            raise KppkValidationError(message)
        product.name = product_name
        stored: object = product_name
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
    elif field == "orgName":
        if link is None:
            message = "Позиция не найдена."
            raise KppkNotFoundError(message)
        org_names = _check_org_names([value])
        org_ids = await _org_ids_by_names(session, org_names)
        org_id = org_ids[0]
        if org_id == link.org_id:
            return org_names[0]
        clash = (
            await session.exec(
                select(KppkOrgProduct).where(
                    col(KppkOrgProduct.product_id) == product.id,
                    col(KppkOrgProduct.org_id) == org_id,
                )
            )
        ).first()
        if clash is not None:
            message = f"Позиция «{product.name}» на этом ДЗО уже существует."
            raise KppkValidationError(message)
        link.org_id = org_id
        link.updated_at = _now()
        link.updater_user_id = user.id
        session.add(link)
        stored = org_names[0]
    elif field == "orgNames":
        # The DEFAULT (grouped) view edits a product's whole ДЗО set in one
        # multi-select cell: added assets become new positions, removed ones drop
        # theirs. The per-position flags of the surviving pairs are kept.
        org_names = _check_org_names(value)
        org_ids = await _org_ids_by_names(session, org_names)
        await replace_org_links(session, product.id, org_ids, updater_user_id=user.id)
        stored = sorted(org_names)
    elif field == "hasExcise":
        if link is None:
            message = "Позиция не найдена."
            raise KppkNotFoundError(message)
        if not isinstance(value, bool):
            message = "Значение поля «Заполняемый акциз» должно быть да/нет."
            raise KppkValidationError(message)
        link.has_excise = value
        link.updated_at = _now()
        link.updater_user_id = user.id
        session.add(link)
        stored = value
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
    """
    Add a product, optionally together with its first ДЗО positions.

    The name may repeat an existing product: then the request adds positions to
    it (and that requires at least one ДЗО). A new name creates the product; its
    ДЗО set may be empty — a product without positions is a legitimate state
    that can be given assets later.
    """
    product_name = _check_product_name(request.name)
    # The grouped (default) view types a product once and multi-selects its ДЗО,
    # so it sends `orgNames` instead of the single `orgName` of the position
    # view; both end up as the same stored positions.
    requested_orgs = _check_org_names(request.orgNames if request.orgNames is not None else [request.orgName])
    now = _now()
    product = await _product_by_name(session, product_name)
    new_product = product is None
    if not new_product and not requested_orgs:
        message = "Выберите хотя бы одно ДЗО."
        raise KppkValidationError(message)
    if product is None:
        product_num = _check_source_number(request.productNum)
        if product_num is not None:
            duplicate = (
                await session.exec(select(KppkProduct).where(col(KppkProduct.product_num) == product_num))
            ).first()
            if duplicate is not None:
                message = f"Номер {product_num} уже используется: «{duplicate.name}»."
                raise KppkValidationError(message)
        product = KppkProduct(
            product_num=product_num,
            name=product_name,
            created_at=now,
            updated_at=now,
            updater_user_id=user.id,
        )
        session.add(product)
        await session.flush()
    org_ids = await _org_ids_by_names(session, requested_orgs)
    existing = {
        link.org_id
        for link in (
            await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == product.id))
        ).all()
        if product.id is not None
    }
    created: list[tuple[str, KppkOrgProduct]] = []
    for org_id, org_name in zip(org_ids, requested_orgs, strict=True):
        if org_id in existing:
            message = f"Позиция «{product.name}» на этом ДЗО уже существует: «{org_name}»."
            raise KppkValidationError(message)
        link = KppkOrgProduct(
            org_id=org_id,
            product_id=product.id,
            has_excise=request.hasExcise,
            created_at=now,
            updated_at=now,
            updater_user_id=user.id,
        )
        session.add(link)
        created.append((org_name, link))
    await session.flush()
    if created:
        first_org_name, first = created[0]
        row = KppkProductPositionRowRead(
            id=first.id,
            productId=product.id,
            productNum=product.product_num,
            name=product.name,
            orgName=first_org_name,
            hasExcise=first.has_excise,
            createdAt=product.created_at,
            updatedAt=product.updated_at,
            updatedBy=product.updated_by,
        )
    else:
        # A product with no positions yet: the row is the PRODUCT (its own cells
        # stay editable), exactly what the grouped view renders.
        row = KppkProductPositionRowRead(
            id=product.id,
            productId=product.id,
            productNum=product.product_num,
            name=product.name,
            orgName="",
            orgNames=[],
            createdAt=product.created_at,
            updatedAt=product.updated_at,
            updatedBy=product.updated_by,
        )
    applied = [
        AppliedCell(
            target_table=KppkOrgProduct.__tablename__,
            row_id=link.id,
            field="__created",
            old_value=None,
            new_value={
                "productId": str(product.id),
                "productNum": row.productNum,
                "name": row.name,
                "orgName": org_name,
                "hasExcise": link.has_excise,
                "newProduct": new_product,
            },
        )
        for org_name, link in created
    ]
    if new_product and not created:
        # Journal the bare product as a product-level creation so undo removes
        # it again (nothing else references an unpositioned product).
        applied.append(
            AppliedCell(
                target_table=KppkProduct.__tablename__,
                row_id=product.id,
                field="__created",
                old_value=None,
                new_value={
                    "productId": str(product.id),
                    "productNum": row.productNum,
                    "name": row.name,
                    "orgName": "",
                    "hasExcise": False,
                    "newProduct": True,
                },
            )
        )
    return row, applied


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_product_position_cell(
    session: AsyncSession,
    *,
    position_id: UUID,
    field: str,
    value: object,
    user: User,
) -> list[AppliedCell]:
    """
    Edit one cell of a product × ДЗО position.

    The row id is the POSITION: «№»/«Наименование» write the product (so the
    product's whole block changes), «ДЗО»/«Заполняемый акциз» write the
    position itself.
    """
    link = await session.get(KppkOrgProduct, position_id)
    if link is None:
        # The grouped view edits a PRODUCT row whose id is the product id: a
        # product can legitimately have no position left (its last asset was
        # removed), and its own cells («№», «Наименование») must stay editable.
        product = await session.get(KppkProduct, position_id)
        if product is None or field not in {"name", "productNum", "orgNames"}:
            message = "Позиция не найдена."
            raise KppkNotFoundError(message)
        old_value = await _position_field_value(session, product=product, link=None, field=field)
        stored = await _apply_position_cell(session, product=product, link=None, field=field, value=value, user=user)
        return [
            AppliedCell(
                target_table=KppkProduct.__tablename__,
                row_id=product.id,
                field=field,
                old_value=old_value,
                new_value=stored,
            )
        ]
    product = await session.get(KppkProduct, link.product_id)
    if product is None:
        message = "Продукт позиции не найден."
        raise KppkNotFoundError(message)
    old_value = await _position_field_value(session, product=product, link=link, field=field)
    stored = await _apply_position_cell(session, product=product, link=link, field=field, value=value, user=user)
    target = KppkOrgProduct if field in {"orgName", "hasExcise"} else KppkProduct
    return [
        AppliedCell(
            target_table=target.__tablename__,
            row_id=position_id if target is KppkOrgProduct else product.id,
            field=field,
            old_value=old_value,
            new_value=stored,
        )
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def delete_product_only(session: AsyncSession, *, product_id: UUID) -> list[AppliedCell]:
    """
    Delete a PRODUCT that has no positions left (the grouped view's row).

    A numbered product refuses: the fact/plan copies still reference it by its
    source number. A product that still holds positions is left to
    `delete_product_position`, which drops them one by one.
    """
    product = await session.get(KppkProduct, product_id)
    if product is None:
        message = "Продукт не найден."
        raise KppkNotFoundError(message)
    if product.product_num is not None:
        message = "У этого продукта есть номер из справочника — его нельзя удалить, пока он используется."
        raise KppkValidationError(message)
    snapshot: dict[str, object] = {
        "productId": str(product.id),
        "productNum": product.product_num,
        "name": product.name,
        "orgName": "",
        "hasExcise": False,
    }
    await session.delete(product)
    await session.flush()
    return [
        AppliedCell(
            target_table=KppkOrgProduct.__tablename__,
            row_id=product_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def delete_product_position(session: AsyncSession, *, position_id: UUID) -> list[AppliedCell]:
    """
    Delete ONE product × ДЗО position.

    The product itself goes only when that position was its last one — the
    dictionary codes the product once, so an empty product is dead data.
    """
    link = await session.get(KppkOrgProduct, position_id)
    if link is None:
        message = "Позиция не найдена."
        raise KppkNotFoundError(message)
    org = await session.get(KppkOrg, link.org_id)
    product = await session.get(KppkProduct, link.product_id)
    snapshot: dict[str, object] = {
        "productId": str(link.product_id),
        "productNum": product.product_num if product is not None else None,
        "name": product.name if product is not None else "",
        "orgName": org.name if org is not None else "",
        "hasExcise": link.has_excise,
    }
    await session.delete(link)
    await session.flush()
    remaining = (
        await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == link.product_id))
    ).all()
    if not remaining and product is not None:
        await session.delete(product)
        await session.flush()
    return [
        AppliedCell(
            target_table=KppkOrgProduct.__tablename__,
            row_id=position_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def replace_product(
    session: AsyncSession,
    *,
    position_id: UUID,
    target_id: UUID | None,
    user: User,
) -> list[AppliedCell]:
    """
    Replace one PRODUCT of «Продуктовые позиции» with another, then delete it.

    `position_id` is whatever id the grid row carries: in the «Настройка
    акциза» view that is a product × ДЗО POSITION, in the grouped (default)
    view the row IS the product — a product may even have no position at all.
    The replacement always works on the PRODUCT: every position of the replaced
    product moves to the target, and a target that already holds the same ДЗО
    keeps exactly one position there (the duplicate is dropped after merging the
    excise flag — a product flagged anywhere stays flagged).

    The asset links follow the same rule as in «Сырьё»: a link that already
    exists on the target is merged, never duplicated, and the replaced product
    must have no numbered positions — the fact/plan copies still reference it
    by its source number. With `target_id=None` the product is deleted as usual.
    """
    link = await session.get(KppkOrgProduct, position_id)
    if link is not None:
        source = await session.get(KppkProduct, link.product_id)
    else:
        # The grouped view's row id is the PRODUCT id itself.
        source = await session.get(KppkProduct, position_id)
    if source is None:
        message = "Позиция не найдена."
        raise KppkNotFoundError(message)
    if target_id is None:
        if link is not None:
            return await delete_product_position(session, position_id=position_id)
        return await delete_product_only(session, product_id=source.id)
    if target_id == source.id:
        message = "Нельзя заменить значение на самое себя."
        raise KppkValidationError(message)
    if source.product_num is not None:
        message = "У этого продукта есть номер из справочника — его нельзя заменять, пока он используется."
        raise KppkValidationError(message)
    target = await session.get(KppkProduct, target_id)
    if target is None:
        message = "Продукт для замены не найден."
        raise KppkNotFoundError(message)

    target_links = {
        item.org_id: item
        for item in (
            await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == target_id))
        ).all()
    }
    source_links = (await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == source.id))).all()
    moved = 0
    for item in source_links:
        existing = target_links.get(item.org_id)
        if existing is None:
            item.product_id = target_id
            item.updated_at = _now()
            item.updater_user_id = user.id
            session.add(item)
            moved += 1
            continue
        # The target already sits in this ДЗО: keep ONE position. The excise
        # flag is a property of the product at that asset, so either side
        # flagging it keeps it flagged.
        if item.has_excise and not existing.has_excise:
            existing.has_excise = True
            existing.updated_at = _now()
            existing.updater_user_id = user.id
            session.add(existing)
        await session.delete(item)
    await session.flush()
    target.updated_at = _now()
    target.updater_user_id = user.id
    session.add(target)
    source_name = source.name
    await session.delete(source)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        message = (
            "Не удалось выполнить замену из-за ограничений уникальности в связанных таблицах — изменения не сохранены."
        )
        raise KppkValidationError(message) from error
    return [
        AppliedCell(
            target_table=KppkOrgProduct.__tablename__,
            row_id=position_id,
            field="__replaced",
            old_value={"name": source_name, "targetId": str(target_id), "moved": moved},
            new_value=None,
        )
    ]


async def replace_raw(session: AsyncSession, *, raw_id: UUID, target_id: UUID | None) -> list[AppliedCell]:
    """
    Replace one raw item with another, then delete the replaced one.

    No table references a raw item in this phase, so the replacement ends as the
    deletion of the replaced row; the target is only checked to exist.
    """
    if target_id is None:
        return await delete_raw(session, raw_id=raw_id)
    if target_id == raw_id:
        message = "Нельзя заменить значение на него же."
        raise KppkValidationError(message)
    target = await session.get(KppkRaw, target_id)
    if target is None:
        message = "Сырьё для замены не найдено."
        raise KppkNotFoundError(message)
    applied = await delete_raw(session, raw_id=raw_id)
    target.updated_at = _now()
    session.add(target)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        message = "Замена нарушила бы ограничения уникальности в связанных таблицах — ни одна запись не изменена."
        raise KppkValidationError(message) from error
    return applied


async def replace_org(session: AsyncSession, *, org_id: UUID, target_id: UUID | None) -> list[AppliedCell]:
    """
    Replace one ДЗО with another, then delete the replaced one.

    Every product position of the replaced ДЗО moves to the target; a product
    already linked to the target is merged — the duplicate link is dropped, so
    the product keeps exactly one position in that ДЗО. A ДЗО carrying a
    source number is protected like a plain deletion: the fact/plan copies
    still reference it. With `target_id=None` the ДЗО is deleted as usual.
    """
    source = await session.get(KppkOrg, org_id)
    if source is None:
        message = "ДЗО не найдено."
        raise KppkNotFoundError(message)
    if target_id is None:
        return await delete_org(session, org_id=org_id)
    if target_id == org_id:
        message = "Нельзя заменить значение на него же."
        raise KppkValidationError(message)
    if source.num is not None:
        message = "Это ДЗО из исходной выгрузки — на него ссылается копия данных факта/плана."
        raise KppkValidationError(message)
    target = await session.get(KppkOrg, target_id)
    if target is None:
        message = "ДЗО для замены не найдено."
        raise KppkNotFoundError(message)
    target_links = (await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.org_id) == target_id))).all()
    target_product_ids = {link.product_id for link in target_links}
    for link in (await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.org_id) == org_id))).all():
        if link.product_id in target_product_ids:
            # The target already holds this product: keep one row, drop the
            # duplicate before the move (the position key is unique).
            await session.delete(link)
            await session.flush()
        else:
            link.org_id = target_id
    source_name = source.name
    source_code = source.code
    target.updated_at = _now()
    session.add(target)
    await session.delete(source)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        message = "Замена нарушила бы ограничения уникальности в связанных таблицах — ни одна запись не изменена."
        raise KppkValidationError(message) from error
    return [
        AppliedCell(
            target_table=KppkOrg.__tablename__,
            row_id=org_id,
            field="__replaced",
            old_value={"code": source_code, "name": source_name, "targetId": str(target_id)},
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
        value_str = normalize_text(str(value or ""))
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
    value = normalize_text(str(name or ""))
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
    value = normalize_text(str(code or ""))
    if not value:
        message = "Укажите код ДЗО."
        raise KppkValidationError(message)
    if len(value) > 20:
        message = "Код ДЗО слишком длинный (до 20 символов)."
        raise KppkValidationError(message)
    return value


def _check_org_name(name: object) -> str:
    value = normalize_text(str(name or ""))
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


async def _apply_position_record(
    session: AsyncSession,
    *,
    row_id: UUID,
    field: str,
    snapshot: object,
    user: User,
) -> None:
    """
    Undo/redo of a created/deleted product × ДЗО position.

    «__created» carries {old: None, new: snapshot}: undo (None) removes the
    position, redo (the snapshot) restores it under its original link id. If the
    product itself was created together with the position (`newProduct`), the
    undo removes it too when nothing else references it. «__deleted» is the
    mirror image.
    """
    link = await session.get(KppkOrgProduct, row_id)
    if snapshot is None:
        if link is None:
            return
        product_id = link.product_id
        await session.delete(link)
        await session.flush()
        # A product with no positions left is dead data — but only drop it when
        # its creation was journaled together with this position, otherwise a
        # historic product could vanish silently.
        remaining = (
            await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == product_id))
        ).all()
        if not remaining:
            product = await session.get(KppkProduct, product_id)
            if product is not None and field == "__created":
                await session.delete(product)
                await session.flush()
        return
    if link is not None:
        return
    if not isinstance(snapshot, dict):
        message = "Некорректные данные для восстановления позиции."
        raise KppkValidationError(message)
    org_names = _check_org_names([snapshot.get("orgName")])
    org_ids = await _org_ids_by_names(session, org_names)
    product_id = UUID(str(snapshot.get("productId")))
    product = await session.get(KppkProduct, product_id)
    if product is None:
        product_name = _check_product_name(snapshot.get("name"))
        number = _check_source_number(snapshot.get("productNum"))
        product = KppkProduct(
            id=product_id,
            product_num=number,
            name=product_name,
            created_at=_now(),
            updated_at=_now(),
            updater_user_id=user.id,
        )
        session.add(product)
        await session.flush()
    session.add(
        KppkOrgProduct(
            id=row_id,
            org_id=org_ids[0],
            product_id=product.id,
            has_excise=bool(snapshot.get("hasExcise")),
            created_at=_now(),
            updated_at=_now(),
            updater_user_id=user.id,
        )
    )
    await session.flush()


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
    name = normalize_text(str(snapshot.get("name") or ""))
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
    if target_table == KppkOrgProduct.__tablename__:
        if field in {"__created", "__deleted"}:
            await _apply_position_record(session, row_id=row_id, field=field, snapshot=value, user=user)
            return
        link = await session.get(KppkOrgProduct, row_id)
        if link is None:
            message = "Позиция не найдена."
            raise KppkNotFoundError(message)
        product = await session.get(KppkProduct, link.product_id)
        if product is None:
            message = "Продукт позиции не найден."
            raise KppkNotFoundError(message)
        await _apply_position_cell(session, product=product, link=link, field=field, value=value, user=user)
        return
    if target_table == KppkProduct.__tablename__:
        if field in {"__created", "__deleted"}:
            await _apply_product_record(session, row_id=row_id, snapshot=value, user=user)
            return
        product = await session.get(KppkProduct, row_id)
        if product is None:
            message = "Позиция не найдена."
            raise KppkNotFoundError(message)
        # A product-level cell is replayed through its FIRST position: the same
        # validator the interactive edit uses.
        link = (await session.exec(select(KppkOrgProduct).where(col(KppkOrgProduct.product_id) == row_id))).first()
        if link is None:
            message = "У продукта нет позиций — правка невозможна."
            raise KppkNotFoundError(message)
        await _apply_position_cell(session, product=product, link=link, field=field, value=value, user=user)
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
