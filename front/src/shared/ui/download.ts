/**
 * Client-side file download helpers shared by the export flows.
 */

export function downloadBlob(blob: Blob, fileName: string): void {
    const url = URL.createObjectURL(blob);
    triggerNativeDownload(url, fileName);
    // Defer the revoke: revoking synchronously right after click() can cancel
    // an in-progress download in some browsers (notably Safari, and big files).
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Hand the file URL to the browser as a normal download.
 *
 * The browser streams it to disk and shows its OWN progress UI — unlike the
 * File System Access / blob flows, which report the byte progress in a site
 * toast and (for a blob) buffer the whole file in memory.
 */
export function triggerNativeDownload(url: string, fileName: string): void {
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
}
