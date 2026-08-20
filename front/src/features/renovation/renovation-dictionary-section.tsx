"use client";

import { toast } from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DeleteRecordDialog, downloadBlob } from "@/features/raw-materials/raw-materials-workspace";
import type { CellUpdate } from "@/shared/api/raw-materials";
import {
    createRenovationAsset,
    createRenovationCatalogOption,
    createRenovationUser,
    deleteRenovationAsset,
    deleteRenovationCatalogOption,
    exportRenovationDictionary,
    getRenovationCatalogOptions,
    getRenovationDictionaryAssets,
    getRenovationDictionaryUsers,
    RENOVATION_ASSETS_SECTION,
    RENOVATION_USERS_SECTION,
    type RenovationAsset,
    type RenovationAssetRow,
    type RenovationCatalog,
    type RenovationCatalogOptionRow,
    type RenovationUserRow,
    updateRenovationAssetCells,
    updateRenovationCatalogOptionCells,
    updateRenovationUserCells,
} from "@/shared/api/renovation";
import { canonicalCellKey, type CollabRoom } from "@/shared/collab/use-collab-room";
import { isValidEmail } from "@/shared/email";
import { getEditableKeys, type ColumnDescriptor, type GridRow } from "@/shared/grid/grid-config";
import { RawMaterialsGrid, type RawMaterialsGridHandle } from "@/shared/grid/raw-materials-grid";
import { useAsyncData } from "@/shared/hooks/use-async-data";

const R_ASSETS_TABLE = "r_assets";
const R_REFERENCE_VALUES_TABLE = "renovation_reference_values";
const R_USERS_TABLE = "app_users";

const PLACEHOLDER_ID_PREFIX = "new";

type DictionarySection = "renovationAssets" | "renovationUsers" | `renovationCatalog:${string}`;

function isDictionarySection(section: string): section is DictionarySection {
    return (
        section === RENOVATION_ASSETS_SECTION ||
        section === RENOVATION_USERS_SECTION ||
        section.startsWith("renovationCatalog:")
    );
}

/** Collab table of a renovation collab section (plan or dictionary). */
export function renovationSectionTable(section: string): string {
    if (section === RENOVATION_ASSETS_SECTION) return R_ASSETS_TABLE;
    if (section === RENOVATION_USERS_SECTION) return R_USERS_TABLE;
    if (section.startsWith("renovationCatalog:")) return R_REFERENCE_VALUES_TABLE;
    return "renovation_plan_rows";
}

function collabTableOf(section: DictionarySection): string {
    return renovationSectionTable(section);
}

function toGridRow<T extends Record<string, unknown> & { id: string }>(row: T): GridRow {
    return {
        ...row,
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        isPlaceholder: false,
    };
}

function toAssetOptions(assets: RenovationAsset[]): Array<{ id: string; name: string }> {
    return assets.map((asset) => ({ id: asset.id, name: asset.name }));
}

function initialPlaceholderDraft(section: DictionarySection): Record<string, unknown> {
    if (section === RENOVATION_USERS_SECTION) {
        return {
            email: "",
            fullname: "",
            role: "asset_worker",
            block: "",
            assetNames: [],
            isActive: true,
        };
    }
    return { name: "", repairCode: "" };
}

function placeholderRow(section: DictionarySection, draft: Record<string, unknown>): GridRow {
    return {
        ...draft,
        id: `${PLACEHOLDER_ID_PREFIX}:${section}`,
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        canonicalCells: {},
        isPlaceholder: true,
    };
}

function placeholderText(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function placeholderPayload(
    section: DictionarySection,
    draft: Record<string, unknown>,
): Record<string, unknown> | null {
    if (section === RENOVATION_ASSETS_SECTION) {
        const name = placeholderText(draft.name);
        const repairCode = placeholderText(draft.repairCode);
        if (!name || !repairCode) return null;
        return { name, repairCode };
    }
    if (section.startsWith("renovationCatalog:")) {
        const label = placeholderText(draft.label);
        return label ? { label } : null;
    }
    const email = placeholderText(draft.email).toLowerCase();
    const fullname = placeholderText(draft.fullname);
    const role = placeholderText(draft.role);
    const block = placeholderText(draft.block);
    const central = role === "admin" || role === "approver";
    const assetNames = Array.isArray(draft.assetNames) ? draft.assetNames : [];
    if (!email || !fullname || !role) return null;
    if (!central && (!block || assetNames.length === 0)) return null;
    return {
        email,
        fullname,
        role,
        block: central ? null : block,
        assetNames: central ? [] : assetNames,
    };
}

function recordLabel(row: Record<string, unknown> | undefined): string | null {
    if (!row) return null;
    const candidates = [row.name, row.fullname, row.email, row.label];
    const value = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
    return typeof value === "string" ? value : null;
}

function errorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : "Неизвестная ошибка.";
}

function stampFileName(base: string, now = new Date()): string {
    const pad2 = (value: number) => String(value).padStart(2, "0");
    const stamp = [
        pad2(now.getDate()),
        ".",
        pad2(now.getMonth() + 1),
        ".",
        now.getFullYear(),
        ", ",
        pad2(now.getHours()),
        ".",
        pad2(now.getMinutes()),
        ".",
        pad2(now.getSeconds()),
    ].join("");
    return `${base} (${stamp}).xlsx`;
}

/**
 * An editable dictionary section of the «Ремонт» project: renovation assets,
 * reference catalogs and project users. Mirrors the «Сырьё» SectionContent:
 * the placeholder row creates records, cells commit per record, delete works
 * on the selected rows, export and undo/redo are section-scoped.
 */
export function RenovationDictionarySection({
    section,
    room,
    assets,
    catalogs,
    collabEnabled,
    refreshKey,
    remoteCellValues,
    onDeleteState,
    onExportState,
    onClipboardStateChange,
    onUndoHistoryChanged,
    onAssetsChanged,
}: {
    section: string;
    room: CollabRoom;
    assets: RenovationAsset[];
    catalogs: RenovationCatalog[];
    collabEnabled: boolean;
    /** Bumped by the workspace when rows change elsewhere (collab rows_changed). */
    refreshKey: number;
    remoteCellValues: ReadonlyMap<string, unknown>;
    onDeleteState: (state: { canDelete: boolean; onDelete: () => void }) => void;
    onExportState: (state: { onExport: () => void }) => void;
    onClipboardStateChange: (state: {
        canPaste: boolean;
        onCopy: () => void;
        onPaste: () => void;
    }) => void;
    /** A dictionary mutation landed: undo/redo counters must be re-read. */
    onUndoHistoryChanged: () => void;
    onAssetsChanged: () => void;
}) {
    const gridRef = useRef<RawMaterialsGridHandle>(null);
    const [reloadKey, setReloadKey] = useState(0);
    const [headerPinned, setHeaderPinned] = useState(false);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [exportPending, setExportPending] = useState(false);
    const [isMutationPending, setIsMutationPending] = useState(false);
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const placeholderCreatePendingRef = useRef(false);
    const [placeholderDraft, setPlaceholderDraft] = useState<Record<string, unknown>>(() =>
        initialPlaceholderDraft(isDictionarySection(section) ? section : RENOVATION_ASSETS_SECTION),
    );
    const placeholderDraftRef = useRef(placeholderDraft);

    const dictSection = isDictionarySection(section) ? section : RENOVATION_ASSETS_SECTION;
    const catalogId = dictSection.startsWith("renovationCatalog:")
        ? dictSection.slice("renovationCatalog:".length)
        : null;
    const sectionLabel =
        dictSection === RENOVATION_ASSETS_SECTION
            ? "Предприятия"
            : dictSection === RENOVATION_USERS_SECTION
              ? "Пользователи"
              : (catalogs.find((catalog) => catalog.id === catalogId)?.label ?? dictSection);

    const loader = useMemo(() => {
        switch (dictSection) {
            case RENOVATION_ASSETS_SECTION:
                return getRenovationDictionaryAssets;
            case RENOVATION_USERS_SECTION:
                return getRenovationDictionaryUsers;
            default:
                return catalogId
                    ? () => getRenovationCatalogOptions(catalogId)
                    : () => Promise.resolve([]);
        }
    }, [catalogId, dictSection]);

    const { data: rows, error } = useAsyncData<
        RenovationAssetRow[] | RenovationUserRow[] | RenovationCatalogOptionRow[]
    >(loader, [loader, reloadKey, refreshKey]);

    const collabTable = collabTableOf(dictSection);
    const showAssetColumn = true;
    const editableFieldKeys = useMemo(
        () =>
            getEditableKeys(
                dictSection,
                true,
                showAssetColumn,
                false,
                catalogDescriptors(catalogId),
            ),
        [dictSection],
    );

    // Section switch: reset the placeholder-row state and selection.
    useEffect(() => {
        const next = initialPlaceholderDraft(dictSection);
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        setSelectedRowIds([]);
        setDeleteOpen(false);
    }, [dictSection]);

    useEffect(() => {
        placeholderDraftRef.current = placeholderDraft;
    }, [placeholderDraft]);

    useEffect(() => {
        if (isMutationPending) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && deleteOpen) {
                setDeleteOpen(false);
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [deleteOpen, isMutationPending]);

    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);

    const gridRows = useMemo<GridRow[]>(() => {
        const base = (rows ?? []).map((row) => {
            const source = row as RenovationAssetRow &
                RenovationUserRow &
                RenovationCatalogOptionRow;
            const gridRow = toGridRow({
                ...source,
                id:
                    dictSection === RENOVATION_ASSETS_SECTION ||
                    dictSection === RENOVATION_USERS_SECTION
                        ? source.id
                        : source.valueId,
            });
            if (!collabEnabled) return gridRow;
            gridRow.canonicalCells = {};
            for (const field of editableFieldKeys) {
                gridRow.canonicalCells[field] = canonicalCellKey(collabTable, gridRow.id, field);
            }
            return gridRow;
        });
        if (collabEnabled) {
            base.push(placeholderRow(dictSection, placeholderDraftRef.current));
        }
        return base;
    }, [collabEnabled, dictSection, editableFieldKeys, rows]);

    // Remote cell values (live edits of other users) override the local ones.
    const displayRows = useMemo(() => {
        if (remoteCellValues.size === 0) return gridRows;
        return gridRows.map((row) => {
            if (row.isPlaceholder || !row.canonicalCells) return row;
            let merged: GridRow | null = null;
            for (const field of editableFieldKeys) {
                const value = remoteCellValues.get(row.canonicalCells[field] ?? "");
                if (value === undefined) continue;
                if (!merged) merged = { ...row };
                merged[field] = value;
            }
            return merged ?? row;
        });
    }, [editableFieldKeys, gridRows, remoteCellValues]);

    const selectedRows = useMemo(
        () => gridRows.filter((row) => selectedRowIds.includes(row.id)),
        [gridRows, selectedRowIds],
    );
    const canDelete = dictSection !== RENOVATION_USERS_SECTION && selectedRows.length > 0;

    const readOnly = isMutationPending || (collabEnabled && room.status === "offline");

    const remoteLocks = useMemo(() => {
        const result = new Map<string, { fullname: string; email: string }>();
        for (const [key, user] of room.lockedCells) {
            result.set(key, { fullname: user.name || "", email: user.email || "" });
        }
        return result;
    }, [room.lockedCells]);

    const remotePresence = useMemo(() => {
        const meId = room.me?.userId ?? null;
        const result = new Map<string, { fullname: string; email: string }>();
        for (const member of room.members) {
            if (member.userId === meId) continue;
            const selection = member.selection as { cellKeys?: unknown } | null | undefined;
            const cellKeys = Array.isArray(selection?.cellKeys)
                ? selection.cellKeys.filter((key): key is string => typeof key === "string")
                : [];
            for (const key of cellKeys) {
                if (!result.has(key)) {
                    result.set(key, { fullname: member.name || "", email: member.email || "" });
                }
            }
        }
        return result;
    }, [room.me?.userId, room.members]);

    const remoteCellInfo = useMemo(() => {
        const meId = room.me?.userId ?? null;
        const result = new Map<string, { name: string; label: string | null }>();
        for (const member of room.members) {
            if (member.userId === meId) continue;
            const selection = member.selection as
                | { label?: unknown; cellKeys?: unknown }
                | null
                | undefined;
            const cellKeys = Array.isArray(selection?.cellKeys)
                ? selection.cellKeys.filter((key): key is string => typeof key === "string")
                : [];
            if (cellKeys.length === 0) continue;
            const name = member.name || member.email || "";
            const label =
                typeof selection?.label === "string" && selection.label ? selection.label : null;
            for (const key of cellKeys) {
                if (!result.has(key)) result.set(key, { name, label });
            }
        }
        for (const [key, user] of room.lockedCells) {
            if (!result.has(key)) {
                result.set(key, { name: user.name || "", label: null });
            }
        }
        return result;
    }, [room.lockedCells, room.me?.userId, room.members]);

    useEffect(() => {
        onDeleteState({ canDelete, onDelete: () => setDeleteOpen(true) });
    }, [canDelete, onDeleteState]);

    const commit = useCallback(
        async (updates: CellUpdate[]): Promise<boolean> => {
            if (readOnly || !updates.length) return true;
            const task = doCommit(updates);
            pendingCommitRef.current = task;
            try {
                return await task;
            } finally {
                if (pendingCommitRef.current === task) pendingCommitRef.current = null;
            }
        },
        [readOnly],
    );

    async function doCommit(updates: CellUpdate[]): Promise<boolean> {
        setIsMutationPending(true);
        try {
            if (dictSection === RENOVATION_ASSETS_SECTION) {
                await updateRenovationAssetCells(updates);
            } else if (dictSection === RENOVATION_USERS_SECTION) {
                // The email is not part of the user PATCH schema — it is only
                // set at creation (placeholder row). Drop stray updates (e.g. a
                // paste into an existing row) instead of failing the batch.
                const userUpdates = updates.filter((update) => update.field !== "email");
                if (userUpdates.length) await updateRenovationUserCells(userUpdates);
            } else if (catalogId) {
                await updateRenovationCatalogOptionCells(catalogId, updates);
            }
            onUndoHistoryChanged();
            refresh();
            return true;
        } catch (reason) {
            toast.danger("Изменения не сохранены", { description: errorMessage(reason) });
            return false;
        } finally {
            setIsMutationPending(false);
        }
    }

    async function createRecord(payload: Record<string, unknown>) {
        if (placeholderCreatePendingRef.current) return;
        placeholderCreatePendingRef.current = true;
        setIsMutationPending(true);
        try {
            if (
                dictSection === RENOVATION_USERS_SECTION &&
                !isValidEmail(String(payload.email ?? ""))
            ) {
                toast.danger("Некорректный email", {
                    description: "Проверьте формат адреса электронной почты.",
                });
                return;
            }
            if (dictSection === RENOVATION_ASSETS_SECTION) {
                await createRenovationAsset(String(payload.name), String(payload.repairCode));
                onAssetsChanged();
            } else if (dictSection === RENOVATION_USERS_SECTION) {
                await createRenovationUser({
                    email: String(payload.email),
                    fullname: String(payload.fullname),
                    role: String(payload.role),
                    block:
                        typeof payload.block === "string" && payload.block
                            ? String(payload.block)
                            : null,
                    assetNames: Array.isArray(payload.assetNames)
                        ? payload.assetNames.map(String)
                        : [],
                });
            } else if (catalogId) {
                await createRenovationCatalogOption(catalogId, String(payload.label));
            }
            refresh();
            const next = initialPlaceholderDraft(dictSection);
            placeholderDraftRef.current = next;
            setPlaceholderDraft(next);
            toast.success("Запись добавлена");
        } catch (reason) {
            toast.danger("Не удалось добавить запись", { description: errorMessage(reason) });
        } finally {
            placeholderCreatePendingRef.current = false;
            setIsMutationPending(false);
        }
    }

    async function commitPlaceholderPatch(patch: Record<string, unknown>): Promise<boolean> {
        if (isMutationPending) return true;
        const next = { ...placeholderDraftRef.current, ...patch };
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        const payload = placeholderPayload(dictSection, next);
        // createRecord itself shows the error and keeps the placeholder-row draft,
        // so no source rollback is needed.
        if (payload) await createRecord(payload);
        return true;
    }

    async function deleteRecords() {
        if (!canDelete) return;
        setIsMutationPending(true);
        try {
            for (const row of selectedRows) {
                if (dictSection === RENOVATION_ASSETS_SECTION) {
                    await deleteRenovationAsset(row.id);
                } else if (catalogId) {
                    await deleteRenovationCatalogOption(catalogId, row.id);
                }
            }
            refresh();
            setDeleteOpen(false);
            setSelectedRowIds([]);
            onUndoHistoryChanged();
            toast.success("Запись удалена");
        } catch (reason) {
            toast.danger("Не удалось удалить запись", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }

    const exportTable = useCallback(async () => {
        if (exportPending) return;
        const state = await gridRef.current?.getExportState();
        if (!state) return;
        setExportPending(true);
        const fileName = stampFileName(sectionLabel);
        try {
            const blob = await exportRenovationDictionary({
                section: dictSection,
                headerPinned,
                columns: state.columns,
                rowIds: null,
                fileName,
            });
            downloadBlob(blob, fileName);
            toast.success("Файл выгружен", { description: fileName });
        } catch (reason) {
            toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
        } finally {
            setExportPending(false);
        }
    }, [dictSection, exportPending, headerPinned, sectionLabel]);

    useEffect(() => {
        onExportState({ onExport: () => void exportTable() });
    }, [exportTable, onExportState]);

    const handleClipboardState = useCallback(
        (state: { canPaste: boolean }) => {
            onClipboardStateChange({
                canPaste: state.canPaste,
                onCopy: () => void gridRef.current?.copySelection(),
                onPaste: () => void gridRef.current?.pasteIntoSelection(),
            });
        },
        [onClipboardStateChange],
    );

    if (error) {
        return <div className="grid-empty">Не удалось загрузить справочник: {String(error)}</div>;
    }

    return (
        <>
            <RawMaterialsGrid
                ref={gridRef}
                section={dictSection}
                rows={displayRows}
                isAdmin
                showAssetColumn
                readOnly={readOnly}
                hasBaseline={false}
                headerPinned={headerPinned}
                onHeaderPinnedChange={setHeaderPinned}
                assets={toAssetOptions(assets)}
                deliveryBases={[]}
                columnDescriptors={catalogDescriptors(catalogId)}
                namespace={`renovation:${dictSection}`}
                onCommit={commit}
                onPlaceholderCommit={commitPlaceholderPatch}
                onCommitFailed={refresh}
                onSelectedRowsChange={(selection) => setSelectedRowIds(selection.ids)}
                onValidationError={(message) =>
                    toast.warning("Некорректное значение", { description: message })
                }
                onClipboardStateChange={handleClipboardState}
                onLockedCellEdit={(_, ownerName) =>
                    toast.warning("Ячейка занята", {
                        description: `Сейчас её занимает ${ownerName}.`,
                    })
                }
                onAcquireLease={room.acquireLeases}
                onReleaseLease={room.releaseLeases}
                remoteLocks={remoteLocks}
                remotePresence={remotePresence}
                remoteCellInfo={remoteCellInfo}
            />
            <DeleteRecordDialog
                isOpen={deleteOpen}
                isPending={isMutationPending}
                label={recordLabel(selectedRows[0] as Record<string, unknown>)}
                count={selectedRows.length}
                onOpenChange={setDeleteOpen}
                onConfirm={() => void deleteRecords()}
            />
        </>
    );
}

function catalogDescriptors(catalogId: string | null): ColumnDescriptor[] | undefined {
    if (catalogId === null) return undefined;
    return [{ key: "label", label: "Значение", width: 380, kind: "text" }];
}
