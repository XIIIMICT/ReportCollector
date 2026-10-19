"use client";

import { useCallback, type RefObject } from "react";

import type { ClipboardActions } from "./text-helpers";

type ClipboardGrid = {
    copySelection: () => unknown;
    pasteIntoSelection: () => unknown;
};

/** Publish stable actions that resolve the mounted grid at click time. */
export function useGridClipboardActions(
    gridRef: RefObject<ClipboardGrid | null>,
    publish: (actions: ClipboardActions) => void,
): (state: { canPaste: boolean }) => void {
    const onCopy = useCallback(() => void gridRef.current?.copySelection(), [gridRef]);
    const onPaste = useCallback(() => void gridRef.current?.pasteIntoSelection(), [gridRef]);
    return useCallback(
        ({ canPaste }: { canPaste: boolean }) => publish({ canPaste, onCopy, onPaste }),
        [onCopy, onPaste, publish],
    );
}
