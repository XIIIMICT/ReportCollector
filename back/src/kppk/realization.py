"""Read/write operations for the КППК «Реализация» slices."""

from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.locking import project_write
from src.kppk.constants import KPPK_PROJECT_KEY, MARKET_CODES
from src.kppk.models import KppkCost, KppkOrg, KppkOrgProduct, KppkProduct, KppkRealization, KppkRealizationPlan

from .core import KppkNotFoundError, KppkValidationError
from .schemas import KppkCostRowRead, KppkCostViewRead, KppkRealizationPeriodRead, KppkRealizationRowRead

COST_ATTRIBUTES = {
    "fullCostBp": "full_cost_bp",
    "productionCostBp": "production_cost_bp",
    "variableCostBp": "variable_cost_bp",
    "fullCostOp": "full_cost_op",
    "productionCostOp": "production_cost_op",
    "variableCostOp": "variable_cost_op",
    "fullCostFact": "full_cost_fact",
    "productionCostFact": "production_cost_fact",
    "variableCostFact": "variable_cost_fact",
}

if TYPE_CHECKING:
    from src.auth.models import User

# Sort key for a product the <Продуктовые позиции> dictionary does not hold: it
# follows every numbered product (numbers start at 1).
MAX_PRODUCT_NUMBER = 1_000_000

REALIZATION_SLICE_FIELDS: dict[str, tuple[str, ...]] = {
    "bp": ("buyoutBpPlan", "fcaPriceBpPlan", "monthBpPlan", "fcaPriceMarketBpPlan", "exciseBp"),
    "op": ("buyoutMaxPlan", "fcaPriceMaxPlan", "monthMaxPlan", "fcaPriceMarketMaxPlan", "exciseMaxPlan"),
    "fact": ("buyoutFact", "fcaPriceFact", "fact", "fcaPriceMarketFact", "exciseFact"),
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
    "exciseBp": "excise_bp",
    "exciseMaxPlan": "excise_max_plan",
    "exciseFact": "excise_fact",
}

# The excise fields of the three slices (one column per plan/fact copy).
EXCISE_FIELDS: frozenset[str] = frozenset({"exciseBp", "exciseMaxPlan", "exciseFact"})

MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


async def read_current_period(
    session: AsyncSession, *, month: int | None = None, year: int | None = None
) -> KppkRealizationPeriodRead:
    """Return the period the «Реализация» form works on."""
    if (month is None) != (year is None):
        message = "Укажите месяц и год вместе."
        raise KppkValidationError(message)
    if month is not None and year is not None:
        period = date(year, month, 1)
    else:
        period = (await session.exec(select(func.max(col(KppkRealizationPlan.period_date))))).one()
        if period is None:
            period = (await session.exec(select(func.max(col(KppkRealization.period_date))))).one()
        if period is None:
            period = (await session.exec(select(func.max(col(KppkCost.period_date))))).one()
        period = period or datetime.now(UTC).date().replace(day=1)
    return KppkRealizationPeriodRead(
        periodDate=period,
        monthName=MONTH_NAMES[period.month - 1],
        yearNum=period.year,
        repDate=date(period.year, period.month, monthrange(period.year, period.month)[1]),
    )


async def read_market_codes(_session: AsyncSession) -> list[str]:
    """Return the fixed market order used by the КППК form."""
    return list(MARKET_CODES)


def _plan_realization_row_read(
    row: KppkRealizationPlan,
    product_num: int | None = None,
) -> KppkRealizationRowRead:
    """Build the API shape of a plan-copy row."""
    return KppkRealizationRowRead(
        id=row.id,
        product=row.product,
        productNum=product_num,
        region=row.region,
        buyoutBpPlan=row.buyout_bp_plan,
        fcaPriceBpPlan=row.fca_price_bp_plan,
        monthBpPlan=row.month_bp_plan,
        fcaPriceMarketBpPlan=row.fca_price_market_bp_plan,
        buyoutMaxPlan=row.buyout_max_plan,
        fcaPriceMaxPlan=row.fca_price_max_plan,
        monthMaxPlan=row.month_max_plan,
        fcaPriceMarketMaxPlan=row.fca_price_market_max_plan,
        exciseBp=row.excise_bp,
        exciseMaxPlan=row.excise_max_plan,
    )


def _fact_realization_row_read(
    row: KppkRealization,
    product_num: int | None = None,
) -> KppkRealizationRowRead:
    """Build the API shape of a fact-copy row."""
    return KppkRealizationRowRead(
        id=row.id,
        product=row.product,
        productNum=product_num,
        region=row.region,
        buyoutFact=row.buyout_fact,
        fcaPriceFact=row.fca_price_fact,
        fact=row.fact,
        fcaPriceMarketFact=row.fca_price_market_fact,
        exciseFact=row.excise_fact,
    )


def _cost_row_read(row: KppkCost, number: int | None) -> KppkCostRowRead:
    return KppkCostRowRead(
        id=row.id,
        product=row.product,
        productNum=number,
        fullCostBp=row.full_cost_bp,
        productionCostBp=row.production_cost_bp,
        variableCostBp=row.variable_cost_bp,
        fullCostOp=row.full_cost_op,
        productionCostOp=row.production_cost_op,
        variableCostOp=row.variable_cost_op,
        fullCostFact=row.full_cost_fact,
        productionCostFact=row.production_cost_fact,
        variableCostFact=row.variable_cost_fact,
    )


async def read_cost_rows(
    session: AsyncSession, *, org_id: UUID, month: int | None = None, year: int | None = None
) -> KppkCostViewRead:
    if await session.get(KppkOrg, org_id) is None:
        message = "ДЗО не найдено."
        raise KppkNotFoundError(message)
    period = await read_current_period(session, month=month, year=year)
    rows = list(
        (
            await session.exec(
                select(KppkCost).where(col(KppkCost.org_id) == org_id, col(KppkCost.period_date) == period.periodDate)
            )
        ).all()
    )
    numbers = await read_product_numbers(session)
    rows.sort(
        key=lambda row: (
            _product_number(numbers, row.product)
            if _product_number(numbers, row.product) is not None
            else MAX_PRODUCT_NUMBER,
            row.product,
        )
    )
    result = [_cost_row_read(row, _product_number(numbers, row.product)) for row in rows]
    return KppkCostViewRead(period=period, rows=result)


async def read_product_numbers(session: AsyncSession) -> dict[str, int]:
    """
    Return the «№» of every «Продуктовые позиции» product, keyed by its name.

    The realization copies carry only the product name, so the dictionary is the
    only place the number can come from. The lookup is by lowercased trimmed
    name (the copies were seeded from the same matrix, but casing and stray
    spaces have drifted); a product the dictionary does not hold keeps NULL.
    """
    products = (await session.exec(select(KppkProduct))).all()
    numbers: dict[str, int] = {}
    for product in products:
        if product.product_num is None:
            continue
        key = " ".join(product.name.casefold().split())
        numbers.setdefault(key, int(product.product_num))
    return numbers


def _product_number(number_map: dict[str, int], product: str) -> int | None:
    key = " ".join(product.casefold().split())
    exact = number_map.get(key)
    if exact is not None:
        return exact
    prefix_matches = [number for name, number in number_map.items() if key.startswith(name + " ")]
    return prefix_matches[0] if len(prefix_matches) == 1 else None


async def read_excise_products(session: AsyncSession, *, org_id: UUID) -> list[str]:
    """
    Return the product names that carry an excise on the given asset.

    The excise is scoped by the «Заполняемый акциз» flag of the product
    POSITION (`kppk_org_products.has_excise`): an asset whose positions are all
    unflagged returns an empty list (the «Реализация» column is hidden), and on
    a flagged asset only the flagged products count. The realization copies hold
    the product by NAME, so the names are returned exactly as the dictionary
    spells them.
    """
    rows = (
        await session.exec(
            select(KppkProduct.name)
            .join(KppkOrgProduct, col(KppkOrgProduct.product_id) == col(KppkProduct.id))
            .where(col(KppkOrgProduct.org_id) == org_id, col(KppkOrgProduct.has_excise).is_(True))
            .order_by(col(KppkProduct.name))
        )
    ).all()
    return list(rows)


async def read_realization_rows(
    session: AsyncSession,
    *,
    slice_id: str,
    org_id: UUID,
    month: int | None = None,
    year: int | None = None,
) -> list[KppkRealizationRowRead]:
    """List one «Реализация» slice for a period and ДЗО, ordered by product number."""
    fields = REALIZATION_SLICE_FIELDS.get(slice_id)
    if fields is None:
        message = "Неизвестный срез «Реализации»."
        raise KppkValidationError(message)
    del fields
    org = await session.get(KppkOrg, org_id)
    if org is None:
        message = "ДЗО не найдено."
        raise KppkNotFoundError(message)
    period = await read_current_period(session, month=month, year=year)
    markets = await read_market_codes(session)
    region_order = {code: index for index, code in enumerate(markets)}
    number_map = await read_product_numbers(session)

    def region_key(row: KppkRealization | KppkRealizationPlan) -> tuple[int, str, int]:
        # Products follow their «№» (the dictionary's order, the same as the
        # «Продуктовые позиции» tab); a name the dictionary does not hold sorts
        # last, alphabetically. The markets keep their own order inside a
        # product.
        number = _product_number(number_map, row.product)
        return (
            number if number is not None else MAX_PRODUCT_NUMBER,
            row.product,
            region_order.get(row.region, len(region_order)),
        )

    if slice_id == "fact":
        fact_rows = list(
            (
                await session.exec(
                    select(KppkRealization).where(
                        col(KppkRealization.org_id) == org.id,
                        col(KppkRealization.period_date) == period.periodDate,
                        col(KppkRealization.rep_date) == period.repDate,
                    )
                )
            ).all()
        )
        fact_rows.sort(key=region_key)
        return [_fact_realization_row_read(row, _product_number(number_map, row.product)) for row in fact_rows]
    plan_rows = list(
        (
            await session.exec(
                select(KppkRealizationPlan).where(
                    col(KppkRealizationPlan.org_id) == org.id,
                    col(KppkRealizationPlan.period_date) == period.periodDate,
                    col(KppkRealizationPlan.rep_date) == period.repDate,
                )
            )
        ).all()
    )
    plan_rows.sort(key=region_key)
    return [_plan_realization_row_read(row, _product_number(number_map, row.product)) for row in plan_rows]


async def read_realization_row_by_id(
    session: AsyncSession,
    *,
    slice_id: str,
    row_id: UUID,
) -> KppkRealizationRowRead | None:
    """Read one realization row after a PATCH."""
    number_map = await read_product_numbers(session)
    if slice_id == "fact":
        row = await session.get(KppkRealization, row_id)
        return None if row is None else _fact_realization_row_read(row, _product_number(number_map, row.product))
    if slice_id in {"bp", "op"}:
        row = await session.get(KppkRealizationPlan, row_id)
        return None if row is None else _plan_realization_row_read(row, _product_number(number_map, row.product))
    message = "Неизвестный срез «Реализации»."
    raise KppkValidationError(message)


def _check_realization_decimal(value: object) -> Decimal | None:
    """Parse a numeric cell; empty clears it (NULL)."""
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


async def _check_excise_scope(
    session: AsyncSession,
    *,
    row: KppkRealization | KppkRealizationPlan,
    field: str,
) -> None:
    """
    Refuse an excise edit for a product the asset does not levy it on.

    The scope is the «Продуктовые позиции» flag (`has_excise`) of THIS product ×
    ДЗО — the same rule the grid applies by making the cell read-only, enforced
    again here so a stale page or a direct API call cannot smuggle a rate into a
    product that has no excise. Clearing the cell (a zero/empty value) stays
    allowed: it is how a wrongly entered rate is removed.
    """
    del field
    levied = await read_excise_products(session, org_id=row.org_id)
    product_key = " ".join(row.product.casefold().split())
    if any(" ".join(name.casefold().split()) == product_key for name in levied):
        return
    message = f"У продукта «{row.product}» на этом ДЗО акциз не предусмотрен."
    raise KppkValidationError(message)


async def _apply_realization_cell(
    session: AsyncSession,
    *,
    slice_id: str,
    row_id: UUID,
    field: str,
    value: object,
) -> tuple[Decimal | None, Decimal | None]:
    """Validate one numeric cell and store it."""
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
    if field in EXCISE_FIELDS and stored:
        # Outside the product × ДЗО scope an excise may not be SET; clearing it
        # stays allowed so a wrongly entered rate can be removed.
        await _check_excise_scope(session, row=row, field=field)
    # An emptied cell stays NULL — "not filled", the same state the source
    # dumps carry. Coercing it to 0 would report a measurement nobody made and
    # make "clear" indistinguishable from "zero".
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
    """Edit one numeric cell under the shared project lock."""
    del user
    return await _apply_realization_cell(session, slice_id=slice_id, row_id=row_id, field=field, value=value)


async def apply_kppk_realization_cell_value(
    session: AsyncSession,
    *,
    target_table: str,
    row_id: UUID,
    field: str,
    value: object,
) -> None:
    """Replay a journaled «Реализация» cell value."""
    if target_table == KppkRealization.__tablename__:
        slice_id = "fact"
    elif target_table == KppkRealizationPlan.__tablename__:
        slice_id = "bp" if field in REALIZATION_SLICE_FIELDS["bp"] else "op"
    else:
        message = f"Неизвестная таблица реализации «{target_table}»."
        raise KppkValidationError(message)
    await _apply_realization_cell(session, slice_id=slice_id, row_id=row_id, field=field, value=value)


@project_write(KPPK_PROJECT_KEY, exclusive=False)
async def update_cost_cell(
    session: AsyncSession, *, row_id: UUID, field: str, value: object
) -> tuple[Decimal | None, Decimal | None]:
    attribute = COST_ATTRIBUTES.get(field)
    if attribute is None:
        message = "Неизвестное поле себестоимости."
        raise KppkValidationError(message)
    row = await session.get(KppkCost, row_id)
    if row is None:
        message = "Строка себестоимости не найдена."
        raise KppkNotFoundError(message)
    old = getattr(row, attribute)
    stored = _check_realization_decimal(value)
    setattr(row, attribute, stored)
    await session.flush()
    return old, stored
