import bundleAnalyzer from "@next/bundle-analyzer";
import type { NextConfig } from "next";

const withBundleAnalyzer = bundleAnalyzer({
    enabled: process.env.ANALYZE === "true",
});

const nextConfig: NextConfig = {
    output: "standalone",
    reactCompiler: true,
    allowedDevOrigins: ["127.0.0.1", "localhost"],
    // The support mailbox has to be readable from CLIENT components too — the
    // error boundary («Что-то пошло не так») renders its own support link and
    // has no server props at all. `env` inlines the value at build time, so the
    // variable stays single-named (`SUPPORT_MAIL`) in `.env` instead of being
    // duplicated as a `NEXT_PUBLIC_` twin. It is a public mailbox by nature.
    env: {
        NEXT_PUBLIC_SUPPORT_MAIL: process.env.SUPPORT_MAIL ?? "",
    },
    async headers() {
        return [
            {
                source: "/(.*)",
                headers: [
                    {
                        key: "X-Content-Type-Options",
                        value: "nosniff",
                    },
                    {
                        key: "X-Frame-Options",
                        value: "DENY",
                    },
                    {
                        key: "Referrer-Policy",
                        value: "strict-origin-when-cross-origin",
                    },
                    {
                        key: "Permissions-Policy",
                        value: "camera=(), microphone=(), geolocation=()",
                    },
                    {
                        key: "Content-Security-Policy",
                        value: "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; img-src 'self' data: blob:; font-src 'self' data:;",
                    },
                ],
            },
        ];
    },
};

export default withBundleAnalyzer(nextConfig);
