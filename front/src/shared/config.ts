export function getBackendInternalUrl(): string {
    const value = process.env.BACKEND_INTERNAL_URL;
    if (!value) {
        throw new Error(
            "Переменная окружения BACKEND_INTERNAL_URL обязательна: адрес бэкенда для server-side запросов.",
        );
    }

    return value;
}
