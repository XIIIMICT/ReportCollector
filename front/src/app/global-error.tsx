"use client";

/**
 * Root error boundary: replaces the whole document when the root layout
 * itself throws. It renders without globals.css and HeroUI, so the markup
 * is plain HTML with inline styles (same visual language as the login page).
 */
export default function GlobalError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    return (
        <html lang="ru">
            <body
                style={{
                    margin: 0,
                    minHeight: "100vh",
                    display: "grid",
                    placeItems: "center",
                    fontFamily: "system-ui, Arial, sans-serif",
                    background: "#f4f5f7",
                    color: "#18202a",
                }}
            >
                <main
                    style={{
                        background: "#fff",
                        border: "1px solid #d6dae0",
                        padding: "28px",
                        width: "min(360px, calc(100vw - 48px))",
                        boxShadow: "0 8px 24px #0001",
                        textAlign: "center",
                    }}
                >
                    <h1 style={{ fontSize: "22px", margin: "0 0 12px" }}>Ошибка приложения</h1>
                    <p style={{ fontSize: "14px", margin: "0 0 22px" }}>
                        ReportCollector не смог загрузиться. Попробуйте обновить страницу.
                    </p>
                    {error.digest ? (
                        <p style={{ fontSize: "12px", color: "#8a919b", margin: "0 0 22px" }}>
                            Код ошибки: {error.digest}
                        </p>
                    ) : null}
                    <button
                        type="button"
                        onClick={reset}
                        style={{
                            display: "block",
                            width: "100%",
                            padding: "11px",
                            border: "0",
                            background: "#1769aa",
                            color: "#fff",
                            font: "inherit",
                            cursor: "pointer",
                        }}
                    >
                        Обновить
                    </button>
                </main>
            </body>
        </html>
    );
}
