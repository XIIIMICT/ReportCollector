import type { ChangeEvent, KeyboardEvent } from "react";

/**
 * Cell editor props
 */
export interface CellEditorProps<T = unknown> {
    value: T;
    onChange: (value: T) => void;
    onBlur?: () => void;
    onKeyDown?: (e: KeyboardEvent) => void;
    autoFocus?: boolean;
    placeholder?: string;
    disabled?: boolean;
    options?: Array<{ value: string; label: string }>;
}

/**
 * Text cell editor
 */
export function TextCellEditor({
    value,
    onChange,
    onBlur,
    onKeyDown,
    autoFocus,
    placeholder,
    disabled,
}: CellEditorProps<string>) {
    return (
        <input
            type="text"
            className="w-full rounded border px-2 py-1 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            value={value ?? ""}
            onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
            onBlur={onBlur}
            onKeyDown={onKeyDown}
            autoFocus={autoFocus}
            placeholder={placeholder}
            disabled={disabled}
        />
    );
}

/**
 * Number cell editor
 */
export function NumberCellEditor({
    value,
    onChange,
    onBlur,
    onKeyDown,
    autoFocus,
    placeholder,
    disabled,
}: CellEditorProps<number>) {
    return (
        <input
            type="number"
            className="w-full rounded border px-2 py-1 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            value={value?.toString() ?? ""}
            onChange={(e: ChangeEvent<HTMLInputElement>) => {
                const num = Number.parseFloat(e.target.value);
                onChange(Number.isNaN(num) ? 0 : num);
            }}
            onBlur={onBlur}
            onKeyDown={onKeyDown}
            autoFocus={autoFocus}
            placeholder={placeholder}
            disabled={disabled}
        />
    );
}

/**
 * Select cell editor
 */
export function SelectCellEditor({
    value,
    onChange,
    onBlur,
    autoFocus,
    options = [],
    disabled,
}: CellEditorProps<string>) {
    return (
        <select
            className="w-full rounded border px-2 py-1 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            value={value ?? ""}
            onChange={(e: ChangeEvent<HTMLSelectElement>) => onChange(e.target.value)}
            onBlur={onBlur}
            autoFocus={autoFocus}
            disabled={disabled}
        >
            <option value="">--</option>
            {options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                    {opt.label}
                </option>
            ))}
        </select>
    );
}

/**
 * Boolean cell editor
 */
export function BooleanCellEditor({ value, onChange, disabled }: CellEditorProps<boolean>) {
    return (
        <input
            type="checkbox"
            className="h-4 w-4 rounded focus:ring-2 focus:ring-blue-500"
            checked={value ?? false}
            onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.checked)}
            disabled={disabled}
        />
    );
}

/**
 * Date cell editor
 */
export function DateCellEditor({
    value,
    onChange,
    onBlur,
    onKeyDown,
    autoFocus,
    disabled,
}: CellEditorProps<string>) {
    return (
        <input
            type="date"
            className="w-full rounded border px-2 py-1 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
            value={value ?? ""}
            onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
            onBlur={onBlur}
            onKeyDown={onKeyDown}
            autoFocus={autoFocus}
            disabled={disabled}
        />
    );
}

/**
 * Editor factory - returns appropriate editor based on column type
 */
export function createCellEditor(
    type: "text" | "number" | "select" | "boolean" | "date",
    props: CellEditorProps,
) {
    switch (type) {
        case "text":
            return <TextCellEditor {...(props as CellEditorProps<string>)} />;
        case "number":
            return <NumberCellEditor {...(props as CellEditorProps<number>)} />;
        case "select":
            return <SelectCellEditor {...(props as CellEditorProps<string>)} />;
        case "boolean":
            return <BooleanCellEditor {...(props as CellEditorProps<boolean>)} />;
        case "date":
            return <DateCellEditor {...(props as CellEditorProps<string>)} />;
        default:
            return <TextCellEditor {...(props as CellEditorProps<string>)} />;
    }
}
