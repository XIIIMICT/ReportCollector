export type UndoState = {
    enabled: boolean;
    offline: boolean;
    canUndo: boolean;
    canRedo: boolean;
    onUndo: () => void;
    onRedo: () => void;
};

export type ArchiveItem = {
    id: string;
    name: string;
    description?: string;
};

export type ArchiveState = {
    items: ArchiveItem[];
    selectedKey: string | null;
    onSelect: (snapshotId: string | null) => void;
};
