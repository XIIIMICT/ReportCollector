import type { ColumnDescriptor, GridRow, SelectOption } from "@/shared/grid/grid-config";

/**
 * The «Ставки» column set.
 *
 * The reference columns are `select` columns whose stored value is the source
 * UUID and whose cell text comes from the row's display field (`assetName`,
 * `countryFromName`, …). The server prefixes a soft-deleted reference with
 * «УДАЛЕНО: », so the cell must render the row's label — not a fresh lookup in
 * the options of the active dictionary.
 */
export type LogisticsRateDescriptorOptions = {
    assets: SelectOption[];
    countries: SelectOption[];
    transportTypes: SelectOption[];
    hazardClasses: SelectOption[];
    /** Vehicle types valid for the row's transport type. */
    vehicleOptionsForRow: (row: GridRow) => SelectOption[];
    /** The «Актив» column is hidden for an asset worker (the server stamps it). */
    includeAsset: boolean;
};

/** Shown on hover over «Тип подвижного состава» until a transport type is set. */
export const VEHICLE_NEEDS_TRANSPORT =
    "Выберите тип транспортного средства, прежде чем заполнить это поле";

function rateNumber(key: string, label: string, width: number, path?: string[]): ColumnDescriptor {
    return {
        key,
        label,
        ...(path && { path }),
        width,
        kind: "number",
        decimals: 2,
    };
}

function rateCalculated(
    key: string,
    label: string,
    width: number,
    path?: string[],
): ColumnDescriptor {
    return {
        key,
        label,
        ...(path && { path }),
        width,
        kind: "calculated",
        fixed: true,
        decimals: 2,
    };
}

export function logisticsRateDescriptors(
    options: LogisticsRateDescriptorOptions,
): ColumnDescriptor[] {
    const assetColumn: ColumnDescriptor[] = options.includeAsset
        ? [
              {
                  key: "assetId",
                  label: "Актив",
                  width: 190,
                  kind: "select",
                  options: options.assets,
                  displayKey: "assetName",
              },
          ]
        : [];
    return [
        ...assetColumn,
        { key: "product", label: "Продукт", width: 170, kind: "text", readOnly: true },
        {
            key: "dangerous",
            label: "Опасный",
            width: 110,
            kind: "boolean",
            readOnly: true,
            fixed: true,
            calculatedText: true,
        },
        {
            key: "hazardClassValue",
            label: "Класс опасности",
            width: 150,
            kind: "select",
            options: options.hazardClasses,
            // No hazard class is a real state («Опасный» = нет), not an empty cell.
            emptyLabel: "Нет",
        },
        {
            key: "priceKind",
            label: "Индикатив",
            width: 140,
            kind: "select",
            options: [
                { value: "INDICATIVE", label: "Индикатив" },
                { value: "PRICE", label: "Прайс" },
            ],
        },
        {
            key: "direction",
            label: "Направление",
            width: 140,
            kind: "text",
            fixed: true,
            calculatedText: true,
        },
        {
            key: "countryFromId",
            label: "Страна отправления",
            width: 200,
            kind: "select",
            options: options.countries,
            displayKey: "countryFromName",
        },
        {
            key: "originPoint",
            label: "Пункт отправления (А)",
            width: 180,
            kind: "text",
            readOnly: true,
        },
        {
            key: "countryToId",
            label: "Страна назначения",
            width: 200,
            kind: "select",
            options: options.countries,
            displayKey: "countryToName",
        },
        {
            key: "destinationPoint",
            label: "Пункт назначения (Б)",
            width: 180,
            kind: "text",
            readOnly: true,
        },
        {
            key: "transportTypeId",
            label: "Тип транспортного средства",
            width: 230,
            kind: "select",
            options: options.transportTypes,
            displayKey: "transportTypeName",
        },
        {
            key: "vehicleTypeId",
            label: "Тип подвижного состава",
            width: 210,
            kind: "select",
            displayKey: "vehicleTypeName",
            optionsForRow: options.vehicleOptionsForRow,
            cellTitle: (row) =>
                String(row.transportTypeId ?? "") === "" ? VEHICLE_NEEDS_TRANSPORT : undefined,
        },
        rateNumber("loadingTons", "Загрузка в ТС, т", 150),
        rateNumber("freightRateRub", "руб./т", 120, ["Ставка фрахта"]),
        rateCalculated("freightRateUsd", "дол./т", 120, ["Ставка фрахта"]),
        rateNumber("pprRateRub", "руб./т", 110, ["Ставка ППР"]),
        rateCalculated("pprRateUsd", "дол./т", 110, ["Ставка ППР"]),
        rateNumber("deliveryRateRub", "руб./т", 110, ["Отправка из А до Б"]),
        rateCalculated("deliveryRateUsd", "дол./т", 110, ["Отправка из А до Б"]),
        rateCalculated("netRateRub", "руб./т", 110, ["Ставка за 1 тонну нетто без НДС"]),
        rateCalculated("netRateUsd", "дол./т", 110, ["Ставка за 1 тонну нетто без НДС"]),
        { key: "note", label: "Примечание", width: 240, kind: "text" },
        rateCalculated("deviationRub", "руб. без НДС", 140, ["Отклонение к предыдущему месяцу"]),
        rateCalculated("deviationUsd", "дол. без НДС", 140, ["Отклонение к предыдущему месяцу"]),
        rateCalculated("deviationPercent", "%", 100, ["Отклонение к предыдущему месяцу"]),
    ];
}
