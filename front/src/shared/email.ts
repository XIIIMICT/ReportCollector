import { z } from "zod";

/** Email format check via Zod (as everywhere in the project). */
export function isValidEmail(value: string): boolean {
    return z.email().safeParse(value.trim()).success;
}
