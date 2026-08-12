import { appendFile } from "node:fs/promises";

type LogLevel = "ERROR" | "INFO" | "WARNING";

export async function logServerEvent(
    event: string,
    message: string,
    details: Record<string, unknown>,
    level: LogLevel = "INFO",
) {
    const line = JSON.stringify({
        event,
        level,
        message,
        module: "reportcollector.front",
        timestamp: new Date().toISOString(),
        ...details,
    });
    console.info(line);

    const logFile = process.env.FRONT_LOG_FILE?.trim();
    if (!logFile) {
        return;
    }

    try {
        await appendFile(logFile, `${line}\n`, "utf8");
    } catch (error) {
        console.error(
            JSON.stringify({
                error: error instanceof Error ? error.message : String(error),
                event: "frontend_log_write_failed",
                level: "ERROR",
                message: "Не удалось записать серверное событие фронтенда в файл.",
                module: "reportcollector.front",
                timestamp: new Date().toISOString(),
            }),
        );
    }
}
