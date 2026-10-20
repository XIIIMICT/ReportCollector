"use client";

/** Read a client-side workspace query value without making URL state a React dependency. */
export function readWorkspaceQuery(name: string): string | null {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get(name);
}

/** Replace one workspace query value while preserving every other parameter. */
export function writeWorkspaceQuery(name: string, value: string | null): void {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (value === null || value === "") url.searchParams.delete(name);
    else url.searchParams.set(name, value);
    window.history.replaceState(
        window.history.state,
        "",
        `${url.pathname}${url.search}${url.hash}`,
    );
}
