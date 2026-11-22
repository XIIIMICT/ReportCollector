"""The «Ставки» main table: reads, cell edits and computed columns."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.locking import project_write
from src.logistics.constants import LOGISTICS_PROJECT_KEY, LOGISTICS_SOURCE_SCHEMA
from src.logistics.core import (
    LogisticsNotFoundError,
    LogisticsSourceUnavailableError,
    LogisticsValidationError,
)
from src.logistics.dictionaries import country_market, qualified_table
from src.logistics.models import LogisticsHazardClass, LogisticsPriceKind, LogisticsRate
from src.logistics.schemas import (
    LogisticsRateCreateRequest,
    LogisticsRateRowRead,
    LogisticsRateViewRead,
)

if TYPE_CHECKING:
    from src.auth.models import User


def _invalid(message: str) -> NoReturn:
    raise LogisticsValidationError(message)


def _missing(message: str) -> NoReturn:
    raise LogisticsNotFoundError(message)


# The FX rate is a constant until the corporate source provides it.
USD_RATE = Decimal("83.90")

_RUB_FIELDS: dict[str, str] = {
    "freightRateRub": "freight_rate_rub",
    "pprRateRub": "ppr_rate_rub",
    "deliveryRateRub": "delivery_rate_rub",
}
_REFERENCE_FIELDS: dict[str, str] = {
    "assetId": "org_id",
    "countryFromId": "country_from_id",
    "countryToId": "country_to_id",
    "transportTypeId": "transport_type_id",
}
RATE_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        *_REFERENCE_FIELDS,
        "hazardClassValue",
        "vehicleTypeId",
        "loadingTons",
        *_RUB_FIELDS,
        "priceKind",
        "note",
    }
)

# Each map is keyed by the source UUID; the tuple carries the display fields
# and the `deleted_at` marker used by the «УДАЛЕНО: » prefix. A country also
# carries its computed market (РФ/БЗ/ДЗ) — the direction is derived from it.
OrgMap = dict[UUID, tuple[str, object]]
CountryMap = dict[UUID, tuple[str, str, object]]
TypeMap = dict[UUID, tuple[str, object]]
VehicleMap = dict[UUID, tuple[str, object]]
MappingMap = dict[UUID, set[UUID]]


def rate_direction(from_market: str | None, to_market: str | None) -> Literal["РФ", "БЗ", "ДЗ", "Импорт", "Экспорт"]:
    """
    Return the computed «Направление».

    A shipment from Russia is classified by the DESTINATION market — «РФ»,
    «БЗ» or «ДЗ» (Russia → Belarus is «БЗ», Russia → Germany is «ДЗ»). Any
    other departure is an import when it arrives in Russia and an export
    otherwise.
    """
    if from_market == "РФ":
        return to_market if to_market in {"РФ", "БЗ", "ДЗ"} else "ДЗ"  # type: ignore[return-value]
    return "Импорт" if to_market == "РФ" else "Экспорт"


def _deleted_label(label: str, deleted_at: object) -> str:
    return f"УДАЛЕНО: {label}" if deleted_at is not None else label


def _usd_dec(value: Decimal) -> Decimal:
    """Convert a rub amount to dollars: RUB / rate, two places, half up."""
    return (value / USD_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _usd(value: Decimal) -> float:
    return float(_usd_dec(value))


def _net_rub(freight: Decimal, ppr: Decimal, delivery: Decimal) -> Decimal:
    """«Ставка за 1 тонну» in rubles: the sum of the three editable rates."""
    return (freight + ppr + delivery).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _net_usd(freight: Decimal, ppr: Decimal, delivery: Decimal) -> float:
    """Sum the three already-computed dollar cells into the net dollar rate."""
    return float((_usd_dec(freight) + _usd_dec(ppr) + _usd_dec(delivery)).quantize(Decimal("0.01")))


def _prev_period(period: date) -> date:
    """Return the first day of the previous month (December wraps a year)."""
    return date(period.year - 1, 12, 1) if period.month == 1 else date(period.year, period.month - 1, 1)


def _rate_nets(row: LogisticsRate) -> tuple[float, float]:
    """Compute the net rates of one row: (rub, dollar)."""
    return (
        float(_net_rub(row.freight_rate_rub, row.ppr_rate_rub, row.delivery_rate_rub)),
        _net_usd(row.freight_rate_rub, row.ppr_rate_rub, row.delivery_rate_rub),
    )


def _rate_key(row: LogisticsRate) -> tuple[UUID, UUID, UUID, UUID, UUID | None]:
    """Return the natural identity of a rate: asset × route × transport × vehicle."""
    return (
        row.org_id,
        row.country_from_id,
        row.country_to_id,
        row.transport_type_id,
        row.vehicle_type_id,
    )


async def _prev_month_nets(
    session: AsyncSession,
    *,
    period: date,
    org_ids: set[UUID],
) -> dict[tuple[UUID, UUID, UUID, UUID, UUID | None], tuple[float, float]]:
    """
    Return the previous month's net rates keyed by the rate's identity.

    A duplicated key (a data anomaly) keeps the LATEST row: the newest entry
    wins as the map is built in `created_at` order.
    """
    prev = _prev_period(period)
    rows = (
        await session.exec(
            select(LogisticsRate)
            .where(col(LogisticsRate.period_date) == prev)
            .where(col(LogisticsRate.org_id).in_(org_ids))
            .order_by(col(LogisticsRate.created_at), col(LogisticsRate.id))
        )
    ).all()
    return {_rate_key(row): _rate_nets(row) for row in rows}


def _decimal(value: object, *, field: str) -> Decimal:
    """Non-negative decimal with at most two places; stored quantized."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        message = f"Некорректное число в поле «{field}»."
        raise LogisticsValidationError(message) from error
    if parsed < 0:
        message = f"Поле «{field}» не может быть отрицательным."
        raise LogisticsValidationError(message)
    quantized = parsed.quantize(Decimal("0.01"))
    if quantized != parsed:
        message = f"Поле «{field}» должно иметь не более двух знаков после запятой."
        raise LogisticsValidationError(message)
    return quantized


async def _require_external_tables(session: AsyncSession, schema: str) -> None:
    """
    Fail with a readable message when the corporate dictionaries are absent.

    The «Ставки» table references them by FOREIGN KEY (migration 0035), so unlike
    the read-only dictionaries there is no fallback here: without `dict` the form
    cannot resolve a single label. The check is one cheap `to_regclass` query.
    """
    tables = ("orgs", "countries", "transport_types", "vehicle_types", "transport_types_vehicle_types")
    checks = " OR ".join(f"to_regclass(:t{index}) IS NULL" for index in range(len(tables)))
    statement = text(f"SELECT {checks}").bindparams(
        **{f"t{index}": f"{schema}.{table}" for index, table in enumerate(tables)}
    )
    # `session.exec` on raw SQL returns a Result (a Row per statement), so the
    # single boolean must be unwrapped — a Row is always truthy.
    missing = (await session.exec(statement)).scalars().one()  # ty: ignore[no-matching-overload]
    if missing:
        message = (
            "Справочники «Логистики» недоступны: в схеме "
            f"{schema} нет нужных таблиц. Загрузите справочники dict "
            "или укажите LOGISTICS_DICT_SCHEMA."
        )
        raise LogisticsSourceUnavailableError(message)


async def _external_maps(
    session: AsyncSession,
) -> tuple[OrgMap, CountryMap, TypeMap, VehicleMap, MappingMap]:
    """Read the corporate dictionaries once per rates page/cell validation."""
    schema = LOGISTICS_SOURCE_SCHEMA
    await _require_external_tables(session, schema)
    org_rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(f"SELECT id, prefix_ru, deleted_at FROM {qualified_table(schema, 'orgs')}")  # noqa: S608 — identifier is whitelisted
        )
    ).all()
    country_rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(
                f"SELECT id, shortname, alpha2, ts, sng, deleted_at "  # noqa: S608 — identifiers are whitelisted
                f"FROM {qualified_table(schema, 'countries')}"
            )
        )
    ).all()
    type_rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(f"SELECT id, shortname, deleted_at FROM {qualified_table(schema, 'transport_types')}")  # noqa: S608 — identifier is whitelisted
        )
    ).all()
    vehicle_rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(f"SELECT id, name, deleted_at FROM {qualified_table(schema, 'vehicle_types')}")  # noqa: S608 — identifier is whitelisted
        )
    ).all()
    mapping_rows = (
        await session.exec(  # ty: ignore[no-matching-overload]
            text(
                "SELECT m.transport_type_id, m.vehicle_type_id "  # noqa: S608 — identifiers are whitelisted
                f"FROM {qualified_table(schema, 'transport_types_vehicle_types')} m "
                f"JOIN {qualified_table(schema, 'vehicle_types')} v "
                "ON v.id = m.vehicle_type_id AND v.deleted_at IS NULL "
                "WHERE m.deleted_at IS NULL"
            )
        )
    ).all()
    mappings: MappingMap = {}
    for transport_id, vehicle_id in mapping_rows:
        mappings.setdefault(transport_id, set()).add(vehicle_id)
    return (
        {row.id: (row.prefix_ru, row.deleted_at) for row in org_rows},
        {row.id: (row.shortname, country_market(row.alpha2, row.ts, row.sng), row.deleted_at) for row in country_rows},
        {row.id: (row.shortname, row.deleted_at) for row in type_rows},
        {row.id: (row.name, row.deleted_at) for row in vehicle_rows},
        mappings,
    )


def _rate_row_read(
    row: LogisticsRate,
    orgs: OrgMap,
    countries: CountryMap,
    transports: TypeMap,
    vehicles: VehicleMap,
    prev_net: tuple[float, float] | None = None,
) -> LogisticsRateRowRead:
    org = orgs.get(row.org_id)
    country_from = countries.get(row.country_from_id)
    country_to = countries.get(row.country_to_id)
    transport = transports.get(row.transport_type_id)
    vehicle = vehicles.get(row.vehicle_type_id) if row.vehicle_type_id is not None else None
    net_rub = float(_net_rub(row.freight_rate_rub, row.ppr_rate_rub, row.delivery_rate_rub))
    net_usd = _net_usd(row.freight_rate_rub, row.ppr_rate_rub, row.delivery_rate_rub)
    if prev_net is None:
        prev_rub = prev_usd = deviation = deviation_usd = None
    else:
        prev_rub, prev_usd = prev_net
        deviation = round(net_rub - prev_rub, 2)
        deviation_usd = round(net_usd - prev_usd, 2)
    # «%» is temporarily the same rub difference; the real percentage
    # formula arrives with the customer's source.
    return LogisticsRateRowRead(
        id=row.id,
        periodDate=row.period_date,
        assetId=row.org_id,
        assetName=_deleted_label(*org) if org is not None else "",
        dangerous=row.hazard_class_value is not None,
        hazardClassValue=row.hazard_class_value,
        product=row.product,
        direction=rate_direction(
            country_from[1] if country_from is not None else None,
            country_to[1] if country_to is not None else None,
        ),
        countryFromId=row.country_from_id,
        countryFromName=_deleted_label(country_from[0], country_from[2]) if country_from is not None else "",
        originPoint=row.origin_point,
        countryToId=row.country_to_id,
        countryToName=_deleted_label(country_to[0], country_to[2]) if country_to is not None else "",
        destinationPoint=row.destination_point,
        transportTypeId=row.transport_type_id,
        transportTypeName=_deleted_label(*transport) if transport is not None else "",
        vehicleTypeId=row.vehicle_type_id,
        vehicleTypeName=_deleted_label(*vehicle) if vehicle is not None else "",
        loadingTons=float(row.loading_tons),
        freightRateRub=float(row.freight_rate_rub),
        freightRateUsd=_usd(row.freight_rate_rub),
        pprRateRub=float(row.ppr_rate_rub),
        pprRateUsd=_usd(row.ppr_rate_rub),
        deliveryRateRub=float(row.delivery_rate_rub),
        deliveryRateUsd=_usd(row.delivery_rate_rub),
        netRateRub=net_rub,
        netRateUsd=net_usd,
        prevNetRateRub=prev_rub,
        prevNetRateUsd=prev_usd,
        deviationRub=deviation,
        deviationUsd=deviation_usd,
        deviationPercent=deviation,
        priceKind=row.price_kind,
        note=row.note,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
        updatedBy=row.updated_by,
    )


async def read_rate_rows(
    session: AsyncSession,
    *,
    period_date: date,
    row_ids: list[UUID] | None = None,
    asset_ids: set[UUID] | None = None,
) -> list[LogisticsRateRowRead]:
    """Return the rows of one period with the computed labels and values."""
    query = (
        select(LogisticsRate)
        .where(col(LogisticsRate.period_date) == period_date)
        .order_by(col(LogisticsRate.created_at), col(LogisticsRate.id))
    )
    if row_ids is not None:
        query = query.where(col(LogisticsRate.id).in_(row_ids))
    if asset_ids is not None:
        # «Работник на активе» never sees another asset's rows.
        query = query.where(col(LogisticsRate.org_id).in_(asset_ids))
    rows = (await session.exec(query)).all()
    orgs, countries, transports, vehicles, _mappings = await _external_maps(session)
    # The month-over-month deviations need the previous month of the SAME
    # natural identity (asset × route × transport × vehicle), scoped to the
    # assets this read is allowed to see (None = the visible set itself).
    scope = asset_ids if asset_ids is not None else {row.org_id for row in rows}
    prev = await _prev_month_nets(session, period=period_date, org_ids=scope)
    return [_rate_row_read(row, orgs, countries, transports, vehicles, prev.get(_rate_key(row))) for row in rows]


async def read_rate_row(session: AsyncSession, rate_id: UUID) -> LogisticsRateRowRead:
    row = await session.get(LogisticsRate, rate_id)
    if row is None:
        _missing("Строка ставки не найдена.")
    orgs, countries, transports, vehicles, _mappings = await _external_maps(session)
    prev = await _prev_month_nets(session, period=row.period_date, org_ids={row.org_id})
    return _rate_row_read(row, orgs, countries, transports, vehicles, prev.get(_rate_key(row)))


async def read_rate_view(
    session: AsyncSession, *, month: int, year: int, asset_ids: set[UUID] | None = None
) -> LogisticsRateViewRead:
    if not 1 <= month <= 12:
        _invalid("Месяц должен быть от 1 до 12.")
    period = date(year, month, 1)
    return LogisticsRateViewRead(
        periodDate=period,
        usdRate=float(USD_RATE),
        rows=await read_rate_rows(session, period_date=period, asset_ids=asset_ids),
    )


def rate_snapshot(row: LogisticsRate) -> dict[str, object]:
    """Return the replayable snapshot of a created row (the undo journal is JSON)."""
    return {
        "id": str(row.id),
        # The creation timestamp keeps a restored (undone delete) row in its
        # place: the period view is ordered by it.
        "createdAt": row.created_at.isoformat(),
        "periodDate": row.period_date.isoformat(),
        "assetId": str(row.org_id),
        "hazardClassValue": row.hazard_class_value,
        "countryFromId": str(row.country_from_id),
        "countryToId": str(row.country_to_id),
        "transportTypeId": str(row.transport_type_id),
        "vehicleTypeId": str(row.vehicle_type_id) if row.vehicle_type_id is not None else None,
        "loadingTons": float(row.loading_tons),
        "freightRateRub": float(row.freight_rate_rub),
        "pprRateRub": float(row.ppr_rate_rub),
        "deliveryRateRub": float(row.delivery_rate_rub),
        # Computed server-side from the three editable rub rates; kept in the
        # snapshot for the journal's readability, never trusted on replay.
        "netRateRub": float(_net_rub(row.freight_rate_rub, row.ppr_rate_rub, row.delivery_rate_rub)),
        "priceKind": row.price_kind.value,
        "note": row.note,
    }


async def _active_reference(
    row_id: object,
    labels: Mapping[UUID, tuple[object, ...]],
    *,
    field: str,
) -> UUID:
    try:
        parsed = UUID(str(row_id))
    except ValueError as error:
        message = f"Некорректное значение в поле «{field}»."
        raise LogisticsValidationError(message) from error
    entry = labels.get(parsed)
    if entry is None:
        message = f"Значение поля «{field}» отсутствует в справочнике."
        raise LogisticsValidationError(message)
    if entry[-1] is not None:
        message = f"Значение поля «{field}» помечено на удаление и не может быть выбрано."
        raise LogisticsValidationError(message)
    return parsed


async def _validate_vehicle(
    *,
    transport_type_id: UUID,
    vehicle_type_id: UUID | None,
    mappings: MappingMap,
    vehicles: VehicleMap,
) -> UUID | None:
    allowed = mappings.get(transport_type_id, set())
    if vehicle_type_id is None:
        if allowed:
            message = "Для выбранного типа транспортного средства нужно выбрать тип подвижного состава."
            raise LogisticsValidationError(message)
        return None
    entry = vehicles.get(vehicle_type_id)
    if entry is None or entry[-1] is not None:
        message = "Выбранный тип подвижного состава отсутствует в справочнике."
        raise LogisticsValidationError(message)
    if vehicle_type_id not in allowed:
        message = "Тип подвижного состава не соответствует выбранному типу транспортного средства."
        raise LogisticsValidationError(message)
    return vehicle_type_id


@project_write(LOGISTICS_PROJECT_KEY, exclusive=False)
async def create_rate(
    session: AsyncSession,
    *,
    request: LogisticsRateCreateRequest,
    editor: User,
) -> LogisticsRate:
    """Insert a complete rate row after validating its references."""
    if request.periodDate.day != 1:
        _invalid("Период ставки должен быть первым числом месяца.")
    if request.assetId is None:
        _invalid("Выберите актив.")
    orgs, countries, transports, vehicles, mappings = await _external_maps(session)
    org_id = await _active_reference(request.assetId, orgs, field="Актив")
    country_from_id = await _active_reference(request.countryFromId, countries, field="Страна отправления")
    country_to_id = await _active_reference(request.countryToId, countries, field="Страна назначения")
    transport_type_id = await _active_reference(request.transportTypeId, transports, field="Тип транспортного средства")
    vehicle_type_id = await _validate_vehicle(
        transport_type_id=transport_type_id,
        vehicle_type_id=request.vehicleTypeId,
        mappings=mappings,
        vehicles=vehicles,
    )
    if request.hazardClassValue is not None:
        hazard = await session.get(LogisticsHazardClass, request.hazardClassValue)
        if hazard is None:
            _invalid("Выбранный класс опасности отсутствует в справочнике.")
    rate = LogisticsRate(
        id=uuid4(),
        period_date=request.periodDate,
        org_id=org_id,
        hazard_class_value=request.hazardClassValue,
        country_from_id=country_from_id,
        country_to_id=country_to_id,
        transport_type_id=transport_type_id,
        vehicle_type_id=vehicle_type_id,
        loading_tons=request.loadingTons,
        freight_rate_rub=request.freightRateRub,
        ppr_rate_rub=request.pprRateRub,
        delivery_rate_rub=request.deliveryRateRub,
        net_rate_rub=_net_rub(request.freightRateRub, request.pprRateRub, request.deliveryRateRub),
        price_kind=request.priceKind,
        product=None,
        origin_point=None,
        destination_point=None,
        note=request.note.strip() if request.note and request.note.strip() else None,
        updater_user_id=editor.id,
    )
    session.add(rate)
    await session.flush()
    return rate


@project_write(LOGISTICS_PROJECT_KEY, exclusive=False)
async def delete_rate(session: AsyncSession, *, rate_id: UUID) -> tuple[date, dict[str, object]]:
    """
    Delete one rate row; return its period and the snapshot the journal needs.

    The row has no dependents yet, so this is a plain delete. The caller
    journals it as an existence action (`__created` with `newValue = None`), so
    undo re-inserts the row and redo removes it again.
    """
    rate = await session.get(LogisticsRate, rate_id)
    if rate is None:
        _missing("Строка ставки не найдена.")
    period = rate.period_date
    snapshot = rate_snapshot(rate)
    await session.delete(rate)
    await session.flush()
    return period, snapshot


async def _update_rate_cell(
    session: AsyncSession,
    *,
    rate_id: UUID,
    field: str,
    value: object,
    editor: User,
    allowed_asset_ids: set[UUID] | None = None,
) -> tuple[object, object]:
    """Apply one editable cell; returns the journal's old/new values."""
    if field not in RATE_EDITABLE_FIELDS:
        _invalid(f"Поле «{field}» недоступно для редактирования.")
    rate = await session.get(LogisticsRate, rate_id)
    if rate is None:
        _missing("Строка ставки не найдена.")
    if allowed_asset_ids is not None and rate.org_id not in allowed_asset_ids:
        # A worker does not learn that another asset's row exists.
        _missing("Строка ставки не найдена.")
    orgs, countries, transports, vehicles, mappings = await _external_maps(session)
    old: object
    new: object
    if field in _REFERENCE_FIELDS:
        labels = {
            "assetId": orgs,
            "countryFromId": countries,
            "countryToId": countries,
            "transportTypeId": transports,
        }[field]
        parsed = await _active_reference(value, labels, field=field)
        attr = _REFERENCE_FIELDS[field]
        old = getattr(rate, attr)
        setattr(rate, attr, parsed)
        new = parsed
        if field == "transportTypeId":
            # The dependent vehicle type cannot survive an incompatible change.
            allowed = mappings.get(parsed, set())
            if rate.vehicle_type_id is not None and rate.vehicle_type_id not in allowed:
                rate.vehicle_type_id = None
    elif field == "vehicleTypeId":
        parsed_vehicle: UUID | None
        if value is None or value == "":
            parsed_vehicle = None
        else:
            try:
                parsed_vehicle = UUID(str(value))
            except ValueError:
                _invalid("Некорректный тип подвижного состава.")
        parsed_vehicle = await _validate_vehicle(
            transport_type_id=rate.transport_type_id,
            vehicle_type_id=parsed_vehicle,
            mappings=mappings,
            vehicles=vehicles,
        )
        old = rate.vehicle_type_id
        rate.vehicle_type_id = parsed_vehicle
        new = parsed_vehicle
    elif field == "hazardClassValue":
        old = rate.hazard_class_value
        if value is None or value == "":
            rate.hazard_class_value = None
        else:
            try:
                hazard_value = int(str(value))
            except ValueError:
                _invalid("Некорректный класс опасности.")
            if not 1 <= hazard_value <= 9 or await session.get(LogisticsHazardClass, hazard_value) is None:
                _invalid("Класс опасности должен быть от 1 до 9.")
            rate.hazard_class_value = hazard_value
        new = rate.hazard_class_value
    elif field == "loadingTons":
        old = rate.loading_tons
        rate.loading_tons = _decimal(value, field="Загрузка в ТС, т")
        new = rate.loading_tons
    elif field in _RUB_FIELDS:
        attr = _RUB_FIELDS[field]
        old = getattr(rate, attr)
        label = {
            "freightRateRub": "Ставка фрахта, руб./т",
            "pprRateRub": "Ставка ППР, руб./т",
            "deliveryRateRub": "Отправка из А до Б, руб./т",
        }[field]
        setattr(rate, attr, _decimal(value, field=label))
        # «Ставка за 1 тонну» is derived, never edited: keep it in step with
        # whichever of the three editable rates just changed.
        rate.net_rate_rub = _net_rub(rate.freight_rate_rub, rate.ppr_rate_rub, rate.delivery_rate_rub)
        new = getattr(rate, attr)
    elif field == "priceKind":
        old = rate.price_kind
        try:
            rate.price_kind = LogisticsPriceKind(str(value))
        except ValueError:
            _invalid("Некорректный индикатив.")
        new = rate.price_kind
    else:
        old = rate.note
        rate.note = str(value).strip() if value not in (None, "") else None
        new = rate.note
    rate.updated_at = datetime.now(UTC)
    rate.updater_user_id = editor.id
    session.add(rate)
    await session.flush()
    # The journal is JSON: UUIDs and Decimals travel as strings/floats.
    if isinstance(old, UUID):
        old = str(old)
    if isinstance(new, UUID):
        new = str(new)
    if isinstance(old, Decimal):
        old = float(old)
    if isinstance(new, Decimal):
        new = float(new)
    if isinstance(old, LogisticsPriceKind):
        old = old.value
    if isinstance(new, LogisticsPriceKind):
        new = new.value
    return old, new


@project_write(LOGISTICS_PROJECT_KEY, exclusive=False)
async def update_rate_cell(
    session: AsyncSession,
    *,
    rate_id: UUID,
    field: str,
    value: object,
    editor: User,
    allowed_asset_ids: set[UUID] | None = None,
) -> tuple[object, object]:
    return await _update_rate_cell(
        session,
        rate_id=rate_id,
        field=field,
        value=value,
        editor=editor,
        allowed_asset_ids=allowed_asset_ids,
    )


async def apply_logistics_rate_value(
    session: AsyncSession,
    *,
    row_id: UUID,
    field: str,
    value: object,
    editor: User,
) -> None:
    """Replay a rate edit (or a create) for the shared undo/redo stack."""
    if field == "__created":
        if value is None:
            row = await session.get(LogisticsRate, row_id)
            if row is not None:
                await session.delete(row)
                await session.flush()
            return
        if not isinstance(value, dict):
            _invalid("Некорректное действие над строкой ставки.")
        if await session.get(LogisticsRate, row_id) is not None:
            return
        created_at = value.get("createdAt")
        freight = Decimal(str(value["freightRateRub"]))
        ppr = Decimal(str(value["pprRateRub"]))
        delivery = Decimal(str(value["deliveryRateRub"]))
        session.add(
            LogisticsRate(
                id=row_id,
                **({"created_at": datetime.fromisoformat(str(created_at))} if isinstance(created_at, str) else {}),
                period_date=date.fromisoformat(str(value["periodDate"])),
                org_id=UUID(str(value["assetId"])),
                hazard_class_value=(
                    int(value["hazardClassValue"]) if value.get("hazardClassValue") is not None else None
                ),
                country_from_id=UUID(str(value["countryFromId"])),
                country_to_id=UUID(str(value["countryToId"])),
                transport_type_id=UUID(str(value["transportTypeId"])),
                vehicle_type_id=(UUID(str(value["vehicleTypeId"])) if value.get("vehicleTypeId") is not None else None),
                loading_tons=Decimal(str(value["loadingTons"])),
                freight_rate_rub=freight,
                ppr_rate_rub=ppr,
                delivery_rate_rub=delivery,
                net_rate_rub=_net_rub(freight, ppr, delivery),
                price_kind=LogisticsPriceKind(str(value["priceKind"])),
                product=None,
                origin_point=None,
                destination_point=None,
                note=value.get("note") if isinstance(value.get("note"), str) else None,
                updater_user_id=editor.id,
            )
        )
        await session.flush()
        return
    await _update_rate_cell(session, rate_id=row_id, field=field, value=value, editor=editor)
