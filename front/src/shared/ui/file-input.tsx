"use client";

import { FieldError, InputGroup, TextField } from "@heroui/react";
import { PagePlusIn } from "iconoir-react";
import { useRef } from "react";

import { ExpandableButton } from "@/shared/ui/expandable-button";

export type FileInputProps = {
    fileName?: string;
    accept?: string;
    placeholder?: string;
    buttonLabel?: string;
    error?: string | null;
    isDisabled?: boolean;
    onFileSelected: (file: File) => void;
};

/**
 * A file picker: a read-only name field with an attached, collapsible
 * «Выбрать файл» button (icon-only until hovered/focused, like the other
 * expandable actions). A press on the field opens the same OS dialog.
 */
export function FileInput({
    fileName = "",
    accept,
    placeholder = "Выберите файл",
    buttonLabel = "Выбрать файл",
    error = null,
    isDisabled = false,
    onFileSelected,
}: FileInputProps) {
    const inputRef = useRef<HTMLInputElement>(null);

    function openPicker() {
        if (!isDisabled) inputRef.current?.click();
    }

    return (
        <>
            <input
                ref={inputRef}
                accept={accept}
                className="sr-only"
                disabled={isDisabled}
                onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    if (file) onFileSelected(file);
                    event.currentTarget.value = "";
                }}
                tabIndex={-1}
                type="file"
            />
            <TextField
                aria-label={placeholder}
                className="file-input"
                fullWidth
                isDisabled={isDisabled}
                isInvalid={error !== null}
            >
                <InputGroup fullWidth variant="secondary">
                    <InputGroup.Input
                        aria-label={placeholder}
                        className="file-input__text"
                        onClick={openPicker}
                        placeholder={placeholder}
                        readOnly
                        value={fileName}
                    />
                    <InputGroup.Suffix className="file-input__suffix">
                        <ExpandableButton
                            isDisabled={isDisabled}
                            onPress={openPicker}
                            variant="tertiary"
                            icon={<PagePlusIn aria-hidden="true" />}
                        >
                            {buttonLabel}
                        </ExpandableButton>
                    </InputGroup.Suffix>
                </InputGroup>
                {error ? <FieldError>{error}</FieldError> : null}
            </TextField>
        </>
    );
}
