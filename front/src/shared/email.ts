import { z } from "zod";

/** Проверка формата email через Zod (как во всём проекте). */
export function isValidEmail(value: string): boolean {
    return z.email().safeParse(value.trim()).success;
}
