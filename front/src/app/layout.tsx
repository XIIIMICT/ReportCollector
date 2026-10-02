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
    // Browser chrome follows the color scheme (next-themes defaults to the
    // system theme, so prefers-color-scheme matches in the common case).
    // Values approximate the theme's --background tokens.
    themeColor: [
        { media: "(prefers-color-scheme: light)", color: "#f5f7f7" },
        { media: "(prefers-color-scheme: dark)", color: "#1c1c1c" },
    ],
};

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html lang="ru" className="h-full antialiased" suppressHydrationWarning>
            <head>
                <link
                    rel="preconnect"
                    href={process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}
                />
                <link
                    rel="dns-prefetch"
                    href={process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}
                />
            </head>
            <body className="bg-background text-foreground h-full font-sans">
                <Providers>{children}</Providers>
            </body>
        </html>
    );
}
