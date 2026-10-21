"use client";

import { useEffect, useRef, useState } from "react";

/** Match SSR on the first render; restore browser-only state after hydration. */
export function useRestoredState<T>(initial: T, restore: () => T) {
    const [value, setValue] = useState<T>(initial);
    const restoreRef = useRef(restore);
    useEffect(() => {
        setValue(restoreRef.current());
    }, []);
    return [value, setValue] as const;
}
