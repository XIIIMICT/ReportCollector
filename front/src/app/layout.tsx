import type { Metadata, Viewport } from "next";

import { Providers } from "./providers";

import "./globals.css";

export const metadata: Metadata = {
    title: "ReportCollector",
    description: "ReportCollector frontend",
    icons: {
        icon: "/ruschem_icon.png",
        apple: "/ruschem_icon.png",
    },
    appleWebApp: {
        capable: true,
        title: "ReportCollector",
    },
};

export const viewport: Viewport = {
    themeColor: "#ffffff",
};

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html lang="ru" className="h-full antialiased" suppressHydrationWarning>
            <body className="bg-background text-foreground min-h-full font-sans">
                <Providers>{children}</Providers>
            </body>
        </html>
    );
}
