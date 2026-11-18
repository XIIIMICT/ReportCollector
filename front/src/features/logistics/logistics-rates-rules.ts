/**
 * The «Ставки» computed rules that the grid previews while the user fills a row.
 *
 * The server is the source of truth (`back/src/logistics/rates.py`); these
 * helpers exist so the draft row can show «Направление» and the dollar cells
 * BEFORE the record exists, and they are unit-tested against the same rule.
 */

export type RateMarket = "РФ" | "БЗ" | "ДЗ";
export type RateDirection = RateMarket | "Импорт" | "Экспорт";

/**
 * Return «Направление».
 *
 * A shipment from Russia is classified by the DESTINATION market — «РФ», «БЗ»
 * or «ДЗ» (Россия → Беларусь is «БЗ», Россия → Германия is «ДЗ»). Any other
 * departure is an import when it arrives in Russia and an export otherwise.
 */
export function rateDirection(
    fromMarket: string | undefined,
    toMarket: string | undefined,
): RateDirection {
    if (fromMarket === "РФ") {
        return toMarket === "РФ" || toMarket === "БЗ" || toMarket === "ДЗ" ? toMarket : "ДЗ";
    }
    return toMarket === "РФ" ? "Импорт" : "Экспорт";
}

/** The dollar equivalent of a rub rate (the constant rate until the source lands). */
export function rateUsd(rub: number, usdRate: number): number {
    return Math.round(rub * usdRate * 100) / 100;
}

/**
 * «Направление» for the DRAFT row and the live re-derivation.
 *
 * A saved rate always carries both countries; the editable row may have
 * neither, and then the computed cell must be EMPTY rather than keep a stale
 * preview (the reported bug: fill both countries, clear them — the old value
 * stayed).
 */
export function rateDirectionPreview(
    fromId: string,
    toId: string,
    marketOf: (countryId: string) => string | undefined,
): RateDirection | "" {
    if (!fromId && !toId) return "";
    return rateDirection(marketOf(fromId), marketOf(toId));
}
