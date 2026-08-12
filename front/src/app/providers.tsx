"use client";

import { Toast } from "@heroui/react";
import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
    return (
        <NextThemesProvider
            attribute="class"
            defaultTheme="system"
            disableTransitionOnChange
            enableSystem
        >
            <Toast.Provider />
            {children}
        </NextThemesProvider>
    );
}
