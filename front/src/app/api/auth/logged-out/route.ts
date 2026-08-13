import { NextResponse } from "next/server";

import { getSessionConfig } from "@/shared/auth/session";

export const dynamic = "force-dynamic";

export function GET() {
    const config = getSessionConfig();
    const loginUrl = new URL("/api/auth/login", config.appOrigin);
    return new NextResponse(loggedOutPage(loginUrl.toString()), {
        headers: { "Content-Type": "text/html; charset=utf-8" },
    });
}

function loggedOutPage(loginUrl: string): string {
    return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Выход из ReportCollector</title><style>body{font-family:Arial,sans-serif;background:#f4f5f7;color:#18202a;margin:0;display:grid;place-items:center;min-height:100vh}main{background:#fff;border:1px solid #d6dae0;padding:28px;width:min(360px,calc(100vw - 48px));box-shadow:0 8px 24px #0001}h1{font-size:22px;margin:0 0 12px}p{font-size:14px;margin:0 0 22px}a{display:block;text-align:center;padding:11px;background:#1769aa;color:#fff;text-decoration:none;font:inherit}</style></head><body><main><h1>Вы вышли из системы</h1><p>Сессия завершена. Для продолжения работы войдите снова.</p><a href="${loginUrl}">Войти</a></main></body></html>`;
}
