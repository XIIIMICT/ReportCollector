"use client";

import { Button, InputGroup, Label, Modal, Spinner, Switch, TextField } from "@heroui/react";
import { Check, Plus, Xmark } from "iconoir-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { SmartSelect } from "@/shared/ui/smart-select";
import { errorMessage } from "@/shared/ui/text-helpers";

export type AddRecordFieldOption = {
    id: string;
    name: string;
    description?: string;
    /** Rendered in the danger color in the dropdown (the «Заблокирован» role). */
    danger?: boolean;
};

export type AddRecordField =
    | {
          kind: "text";
          key: string;
          label: string;
          placeholder?: string;
          autoComplete?: string;
      }
    | {
          kind: "boolean";
          key: string;
          label: string;
          description?: string;
      }
    | {
          kind: "select";
          key: string;
          label: string;
          placeholder?: string;
          options: AddRecordFieldOption[];
      }
    | {
          kind: "multi";
          key: string;
          label: string;
          placeholder?: string;
          options: AddRecordFieldOption[];
      };

export type AddRecordValues = Record<string, unknown>;

export type AddRecordModalProps = {
    isOpen: boolean;
    title: string;
    description?: string;
    fields: AddRecordField[];
    /** Initial field values; also used to reset the form on every open. */
    defaults: AddRecordValues;
    isPending: boolean;
    submitLabel?: string;
    /**
     * Per-field visibility predicate — evaluated against the current values.
     * This is how role-dependent forms hide fields that don't apply (e.g. the
     * responsibility block and the asset picker for a central role).
     */
    isVisible?: (field: AddRecordField, values: AddRecordValues) => boolean;
    /** Returns an error message, or null when the values are valid. */
    validate?: (values: AddRecordValues) => string | null;
    /** Throw to keep the dialog open and show the message in the form. */
    onSubmit: (values: AddRecordValues) => Promise<void>;
    onOpenChange: (open: boolean) => void;
};

export function AddRecordModal({
    isOpen,
    title,
    description,
    fields,
    defaults,
    isPending,
    submitLabel = "Добавить",
    isVisible,
    validate,
    onSubmit,
    onOpenChange,
}: AddRecordModalProps) {
    const [values, setValues] = useState<AddRecordValues>(defaults);
    const [error, setError] = useState<string | null>(null);
    const defaultsRef = useRef(defaults);
    defaultsRef.current = defaults;

    useEffect(() => {
        if (!isOpen) return;
        setValues(defaultsRef.current);
        setError(null);
    }, [isOpen]);

    const visibleFields = useMemo(
        () => (isVisible ? fields.filter((field) => isVisible(field, values)) : fields),
        [fields, isVisible, values],
    );

    function setValue(key: string, value: unknown) {
        setValues((current) => ({ ...current, [key]: value }));
    }

    async function submit() {
        const message = validate?.(values) ?? null;
        if (message) {
            setError(message);
            return;
        }
        setError(null);
        try {
            await onSubmit(values);
        } catch (reason) {
            setError(errorMessage(reason));
        }
    }

    return (
        <Modal.Backdrop
            isOpen={isOpen}
            onOpenChange={onOpenChange}
            isDismissable
            isKeyboardDismissDisabled={false}
        >
            <Modal.Container>
                <Modal.Dialog className="add-record-modal">
                    <Modal.CloseTrigger />
                    <Modal.Header className="add-record-modal__header">
                        <Plus aria-hidden="true" />
                        <Modal.Heading className="add-record-modal__title">{title}</Modal.Heading>
                    </Modal.Header>
                    <Modal.Body className="add-record-modal__body">
                        {description ? (
                            <p className="add-record-modal__hint">{description}</p>
                        ) : null}
                        {visibleFields.map((field) => (
                            <FieldControl
                                key={field.key}
                                field={field}
                                value={values[field.key]}
                                isDisabled={isPending}
                                onChange={(value) => setValue(field.key, value)}
                            />
                        ))}
                        {error ? (
                            <div className="form-error" role="alert">
                                {error}
                            </div>
                        ) : null}
                    </Modal.Body>
                    <Modal.Footer>
                        <Button
                            variant="tertiary"
                            onPress={() => onOpenChange(false)}
                            isDisabled={isPending}
                        >
                            <Xmark aria-hidden="true" />
                            Отменить
                        </Button>
                        <Button
                            variant="primary"
                            onPress={() => void submit()}
                            isDisabled={isPending}
                        >
                            {isPending ? <Spinner color="current" size="sm" /> : <Check />}
                            {submitLabel}
                        </Button>
                    </Modal.Footer>
                </Modal.Dialog>
            </Modal.Container>
        </Modal.Backdrop>
    );
}

function FieldControl({
    field,
    value,
    isDisabled,
    onChange,
}: {
    field: AddRecordField;
    value: unknown;
    isDisabled: boolean;
    onChange: (value: unknown) => void;
}) {
    if (field.kind === "boolean") {
        return (
            <Switch
                isSelected={value === true}
                isDisabled={isDisabled}
                onChange={(selected: boolean) => onChange(selected)}
            >
                <Switch.Content>
                    <Switch.Control>
                        <Switch.Thumb />
                    </Switch.Control>
                    {field.label}
                </Switch.Content>
            </Switch>
        );
    }

    if (field.kind === "select") {
        // The shared dropdown: ≤5 options render a plain Select, more a
        // searchable Autocomplete (hover opens either).
        return (
            <SmartSelect
                label={field.label}
                options={field.options.map((option) => ({
                    id: option.id,
                    name: option.name,
                    ...(option.description && { description: option.description }),
                    ...(option.danger && { danger: true }),
                }))}
                value={typeof value === "string" && value ? value : null}
                isDisabled={isDisabled}
                placeholder={field.placeholder ?? "Выберите значение"}
                onChange={(next) => onChange(typeof next === "string" ? next : "")}
            />
        );
    }

    if (field.kind === "multi") {
        return (
            <MultiField
                field={field}
                value={Array.isArray(value) ? (value as string[]) : []}
                isDisabled={isDisabled}
                onChange={onChange}
            />
        );
    }

    return (
        <TextField
            fullWidth
            value={typeof value === "string" ? value : ""}
            isDisabled={isDisabled}
            onChange={(next) => onChange(next)}
        >
            <Label>{field.label}</Label>
            <InputGroup fullWidth variant="secondary">
                <InputGroup.Input
                    {...(field.autoComplete && { autoComplete: field.autoComplete })}
                    {...(field.placeholder && { placeholder: field.placeholder })}
                />
            </InputGroup>
        </TextField>
    );
}

function MultiField({
    field,
    value,
    isDisabled,
    onChange,
}: {
    field: Extract<AddRecordField, { kind: "multi" }>;
    value: string[];
    isDisabled: boolean;
    onChange: (value: unknown) => void;
}) {
    // One shared dropdown: a short list becomes a plain Select, a long one a
    // searchable Autocomplete — `multiple` works in both.
    return (
        <SmartSelect
            label={field.label}
            options={field.options.map((option) => ({ id: option.id, name: option.name }))}
            value={value}
            multiple
            isDisabled={isDisabled}
            placeholder={field.placeholder ?? "Выберите значения"}
            onChange={(next) => onChange(Array.isArray(next) ? next : [])}
        />
    );
}
