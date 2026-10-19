"use client";

import { useEffect } from "react";

import { isFromTextEditor } from "@/shared/keyboard";

/**
 * The action-bar shortcuts every workspace advertises on its buttons.
 *
 * They were copy-pasted into four files; the hand-rolled copies also differed
 * in how they decided "the key belongs to a text editor" (two of them checked
 * `document.activeElement` instead of the event's `composedPath`, so a key
 * pressed inside a revo-grid cell editor leaked through to the grid).
 *
 * `event.code` is used, not `event.key` — otherwise the shortcuts break on the
 * Russian layout (`AGENTS.md`, landmine 5).
 */

/** Ctrl+Delete opens the delete confirmation (the action-bar button's hint). */
export function useDeleteShortcut(enabled: boolean, onTrigger: () => void): void {
    useEffect(() => {
        if (!enabled) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.code !== "Delete" || !(event.ctrlKey || event.metaKey)) return;
            if (isFromTextEditor(event)) return;
            event.preventDefault();
            onTrigger();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [enabled, onTrigger]);
}

/**
 * Ctrl+Z / Ctrl+Shift+Z (and Ctrl+Y) drive the server-side undo/redo journal.
 *
 * `enabled` is false for an approver (no edit history to walk), on the archive
 * version (read-only) and while the workspace is offline.
 *
 * The two hand-rolled copies in the «Сырьё» and «Ремонт» workspaces hard-coded
 * a `tagName === INPUT/TEXTAREA` check that cannot see revo-grid's shadow-DOM
 * cell editors — an undo typed inside a cell editor reached the journal.
 */
export function useUndoRedoShortcuts({
    enabled,
    onUndo,
    onRedo,
}: {
    enabled: boolean;
    onUndo: () => void;
    onRedo: () => void;
}): void {
    useEffect(() => {
        if (!enabled) return;
        const onKeyDown = (event: KeyboardEvent) => {
            // AltGr arrives as ctrl+alt on Windows — never treat it as Ctrl+Z.
            if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
            if (event.code !== "KeyZ" && event.code !== "KeyY") return;
            if (isFromTextEditor(event)) return;
            event.preventDefault();
            if (event.code === "KeyY" || event.shiftKey) onRedo();
            else onUndo();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [enabled, onRedo, onUndo]);
}
