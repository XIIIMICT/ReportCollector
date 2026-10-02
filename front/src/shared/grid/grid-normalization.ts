/**
 * Grid value normalization utilities for parsing and validating user input
 */

import { parseNumericText } from "./grid-edit";

export type NormalizeResult<T = unknown> = { ok: true; value: T } | { ok: false; message: string };

/**
 * Normalize a numeric value from user input
 * Accepts Excel-style formatting with spaces as thousands separators and comma as decimal
 */
export function normalizeNumber(
    value: unknown,
    options: {
        allowNegative?: boolean;
        allowNull?: boolean;
        fieldLabel?: string;
    } = {},
): NormalizeResult<number | null> {
    const { allowNegative = false, allowNull = true, fieldLabel } = options;

    if (value === null || value === undefined || String(value).trim() === "") {
        if (allowNull) {
            return { ok: true, value: null };
        }
        return {
            ok: false,
            message: fieldLabel ? `Заполните поле «${fieldLabel}».` : "Заполните поле.",
        };
    }

    const parsed = typeof value === "number" ? value : parseNumericText(value);

    if (parsed === null || !Number.isFinite(parsed)) {
        return {
            ok: false,
            message: fieldLabel
                ? `Введите число в ячейку «${fieldLabel}».`
                : "Введите корректное число.",
        };
    }

    if (!allowNegative && parsed < 0) {
        return {
            ok: false,
            message: fieldLabel
                ? `Число в ячейке «${fieldLabel}» должно быть неотрицательным.`
                : "Введите неотрицательное число.",
        };
    }

    return { ok: true, value: parsed };
}

/**
 * Normalize a boolean value from user input
 * Accepts "да"/"нет", "true"/"false", "1"/"0"
 */
export function normalizeBoolean(
    value: unknown,
    options: {
        allowNull?: boolean;
        fieldLabel?: string;
    } = {},
): NormalizeResult<boolean | null> {
    const { allowNull = false, fieldLabel } = options;

    if (typeof value === "boolean") {
        return { ok: true, value };
    }

    const normalized = String(value).trim().toLocaleLowerCase("ru-RU");

    if (normalized === "" && allowNull) {
        return { ok: true, value: null };
    }

    if (["да", "true", "1"].includes(normalized)) {
        return { ok: true, value: true };
    }

    if (["нет", "false", "0", ""].includes(normalized)) {
        return { ok: true, value: allowNull && normalized === "" ? null : false };
    }

    return {
        ok: false,
        message: fieldLabel
            ? `Выберите «Да» или «Нет» в ячейке «${fieldLabel}».`
            : "Выберите «Да» или «Нет».",
    };
}

/**
 * Normalize a text value from user input
 */
export function normalizeText(
    value: unknown,
    options: {
        allowNull?: boolean;
        trim?: boolean;
    } = {},
): NormalizeResult<string | null> {
    const { allowNull = true, trim = true } = options;

    if (value === null || value === undefined) {
        return { ok: true, value: allowNull ? null : "" };
    }

    const text = String(value);
    const normalized = trim ? text.trim() : text;

    if (!normalized && !allowNull) {
        return { ok: false, message: "Заполните поле." };
    }

    return { ok: true, value: normalized || (allowNull ? null : "") };
}

/**
 * Normalize a reference/dictionary value from user input
 * Matches against available options (case-insensitive)
 */
export function normalizeReference<T extends { name: string }>(
    value: unknown,
    options: T[],
    fieldLabel?: string,
): NormalizeResult<T | null> {
    if (value === null || value === undefined || String(value).trim() === "") {
        return { ok: true, value: null };
    }

    const requested = String(value).trim();
    const byName = new Map(
        options
            .filter((opt) => Boolean(opt.name))
            .map((opt) => [opt.name.toLocaleLowerCase("ru-RU"), opt]),
    );

    const canonical = byName.get(requested.toLocaleLowerCase("ru-RU"));

    if (!canonical) {
        return {
            ok: false,
            message: fieldLabel
                ? `Значение «${requested}» отсутствует в справочнике поля «${fieldLabel}».`
                : `Значение «${requested}» отсутствует в справочнике.`,
        };
    }

    return { ok: true, value: canonical };
}

/**
 * Normalize multiple reference values from comma-separated input
 */
export function normalizeMultiReference<T extends { name: string }>(
    value: unknown,
    options: T[],
    fieldLabel?: string,
): NormalizeResult<T[]> {
    const requested = Array.isArray(value)
        ? value.map(String)
        : String(value ?? "")
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean);

    if (requested.length === 0) {
        return { ok: true, value: [] };
    }

    const byName = new Map(
        options
            .filter((opt) => Boolean(opt.name))
            .map((opt) => [opt.name.toLocaleLowerCase("ru-RU"), opt]),
    );

    const canonical: T[] = [];
    const missing: string[] = [];

    for (const req of requested) {
        const match = byName.get(req.toLocaleLowerCase("ru-RU"));
        if (match) {
            canonical.push(match);
        } else {
            missing.push(req);
        }
    }

    if (missing.length > 0) {
        const missingList = missing.map((m) => `«${m}»`).join(", ");
        return {
            ok: false,
            message: fieldLabel
                ? `Значения ${missingList} отсутствуют в справочнике поля «${fieldLabel}».`
                : `Значения ${missingList} отсутствуют в справочнике.`,
        };
    }

    return { ok: true, value: canonical };
}

/**
 * Normalize date from user input
 * Accepts DD.MM.YYYY or YYYY-MM-DD formats
 */
export function normalizeDate(text: string): string | null {
    const ddmmyyyy = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/;
    const match = ddmmyyyy.exec(text);
    if (match) {
        const [, dd, mm, yyyy] = match;
        const day = dd!.padStart(2, "0");
        const month = mm!.padStart(2, "0");
        return `${yyyy}-${month}-${day}`;
    }

    const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/;
    const isoMatch = iso.exec(text);
    if (isoMatch) {
        const [, yyyy, mm, dd] = isoMatch;
        const month = mm!.padStart(2, "0");
        const day = dd!.padStart(2, "0");
        return `${yyyy}-${month}-${day}`;
    }

    return null;
}

export function normalizeDateValue(
    value: unknown,
    fieldLabel?: string,
): NormalizeResult<string | null> {
    if (value === null || value === undefined || String(value).trim() === "") {
        return { ok: true, value: null };
    }

    const iso = normalizeDate(String(value).trim());

    if (!iso) {
        return {
            ok: false,
            message: fieldLabel
                ? `Введите дату в ячейку «${fieldLabel}» в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.`
                : "Введите дату в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.",
        };
    }

    return { ok: true, value: iso };
}
