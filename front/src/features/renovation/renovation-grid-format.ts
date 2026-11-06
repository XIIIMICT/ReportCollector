/**
 * Cell rendering for the renovation plan grid: numbers, dates, datetimes.
 *
 * Split out of `renovation-grid.tsx`. The plan is a Russian-language form, so
 * display formatting is ru-RU throughout while the stored values stay ISO
 * (`YYYY-MM-DD`, full ISO for datetimes) — the converters below bridge the two
 * and never trust the shape blindly (a legacy cell may hold anything). A
 * datetime is an instant and is printed in the browser's local zone; a date is
 * a calendar day and is taken from its own parts, so neither shifts wrongly.
 */

import type { HyperFunc, VNode } from "@revolist/react-datagrid";

import { renderDateTimeText } from "@/shared/grid/grid-config";

/** Right-aligned numeric cell with the field's own decimal count. */
export function numberTemplate(decimals: number | null | undefined) {
    const digits = decimals ?? 0;
    const formatter = new Intl.NumberFormat("ru-RU", {
        minimumFractionDigits: digits,
        maximumFractionDigits: Math.max(digits, 6),
    });
    return (h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode => {
        if (value === null || value === undefined || value === "") return h("span", {});
        const parsed = typeof value === "number" ? value : Number(String(value).replace(",", "."));
        const label = Number.isFinite(parsed) ? formatter.format(parsed) : String(value);
        return h("span", { class: "report-cell__number" }, label);
    };
}

// Unified date/datetime rendering (Russian): дд.мм.гггг and дд.мм.гггг, чч:мм:сс.
// Date values arrive as 'YYYY-MM-DD'; datetimes as a full ISO string.
export function formatPlanDate(value: unknown): string {
    if (value === null || value === undefined || value === "") return "";
    const s = String(value);
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s.trim());
    if (m) return `${m[3]}.${m[2]}.${m[1]}`;
    const parsed = new Date(s);
    return Number.isNaN(parsed.getTime())
        ? s
        : new Intl.DateTimeFormat("ru-RU", {
              day: "2-digit",
              month: "2-digit",
              year: "numeric",
          }).format(parsed);
}

export function formatPlanDateTime(value: unknown): string {
    if (value === null || value === undefined || value === "") return "";
    // A timestamp is an INSTANT: parse it and let `Intl` print it in the
    // browser's zone. Slicing the ISO string used to print the server's UTC
    // clock as if it were local — «Время добавления» showed GMT+0 while every
    // other form showed GMT+3. `renderDateTimeText` is the shared rule.
    if (typeof value === "string") return renderDateTimeText(value);
    const parsed = value instanceof Date ? value : new Date(String(value));
    return Number.isNaN(parsed.getTime())
        ? String(value)
        : new Intl.DateTimeFormat("ru-RU", {
              day: "2-digit",
              month: "2-digit",
              year: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
          }).format(parsed);
}

export function dateTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    const label = formatPlanDate(value);
    return label ? h("span", {}, label) : h("span", {});
}

export function dateTimeTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    const label = formatPlanDateTime(value);
    return label ? h("span", {}, label) : h("span", {});
}

/** `дд.мм.гггг` (or an ISO date) → `YYYY-MM-DD`; `null` when it is not a date. */
export function normalizeDate(text: string): string | null {
    const dotted = text.split(".");
    if (dotted.length === 3) {
        const day = Number(dotted[0]);
        const month = Number(dotted[1]);
        const year = Number(dotted[2]);
        if (
            Number.isInteger(day) &&
            Number.isInteger(month) &&
            Number.isInteger(year) &&
            day >= 1 &&
            day <= 31 &&
            month >= 1 &&
            month <= 12 &&
            year >= 1900 &&
            year <= 2100
        ) {
            const pad = (item: number): string => String(item).padStart(2, "0");
            return `${year}-${pad(month)}-${pad(day)}`;
        }
        return null;
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
    return null;
}
