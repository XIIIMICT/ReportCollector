"use client";

/**
 * File System Access API helpers (Chromium).
 *
 * A large export should behave like a normal big download: the save dialog
 * opens immediately and the user sees a real percentage while the bytes land.
 * `showSaveFilePicker` lets us do exactly that — ask for the destination up
 * front, then stream the response into the chosen file. Browsers without the
 * API fall back to a regular blob download (`downloadBlob`).
 *
 * The API is not in the standard DOM lib (WICG proposal), so the minimal
 * surface we use is typed here.
 */

export type SaveFileHandle = {
    createWritable: () => Promise<{
        write: (data: Uint8Array) => Promise<void>;
        close: () => Promise<void>;
        abort?: () => Promise<void>;
    }>;
};

type SaveFilePickerOptions = {
    suggestedName?: string;
    types?: Array<{ description?: string; accept: Record<string, string[]> }>;
};

type SaveFilePicker = (options: SaveFilePickerOptions) => Promise<SaveFileHandle>;

function getPicker(): SaveFilePicker | null {
    if (typeof window === "undefined") return null;
    const picker = (window as unknown as { showSaveFilePicker?: unknown }).showSaveFilePicker;
    return typeof picker === "function" ? (picker as SaveFilePicker).bind(window) : null;
}

export function hasSaveFilePicker(): boolean {
    return getPicker() !== null;
}

/**
 * Ask the user where to save the file. Throws the picker's `AbortError` when
 * the user cancels (the caller treats it as "stop, do nothing").
 */
export async function pickSaveFile(suggestedName: string): Promise<SaveFileHandle> {
    const picker = getPicker();
    if (picker === null) {
        throw new Error("Браузер не поддерживает выбор места сохранения.");
    }
    return picker({
        suggestedName,
        types: [
            {
                description: "Excel",
                accept: {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
                },
            },
        ],
    });
}

/**
 * Stream a URL's body into the chosen file, reporting byte progress.
 *
 * The response must carry `Content-Length` for a percentage (the backend
 * `FileResponse` and the Next proxy both forward it).
 */
export async function streamUrlToFileHandle(
    handle: SaveFileHandle,
    url: string,
    onProgress: (received: number, total: number) => void,
): Promise<void> {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok || !response.body) {
        throw new Error(`Не удалось скачать файл (${response.status}).`);
    }
    const total = Number(response.headers.get("Content-Length") ?? 0);
    const writable = await handle.createWritable();
    const reader = response.body.getReader();
    let received = 0;
    try {
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            if (!value) continue;
            await writable.write(value);
            received += value.length;
            onProgress(received, total);
        }
        await writable.close();
    } catch (error) {
        await writable.abort?.().catch(() => {});
        throw error;
    }
}
