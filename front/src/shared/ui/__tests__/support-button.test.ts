import { describe, expect, test } from "bun:test";

import { SUPPORT_BODY_INTRO, supportMailto } from "../support-button";

/**
 * «Связаться с разработчиком» opens a `mailto:` that a mail client must be
 * able to parse: the recipient and subject are plain, the body is the fixed
 * letterhead the user is asked to extend.
 */
describe("supportMailto", () => {
    const decode = (value: string) => decodeURIComponent(value.replace(/\+/g, " "));

    test("addresses the configured mailbox", () => {
        expect(supportMailto("support@example.ru", "Ремонт")).toStartWith(
            "mailto:support@example.ru?",
        );
    });

    test("names the project in the subject", () => {
        const url = supportMailto("support@example.ru", "КППК");
        const subject = new URLSearchParams(url.split("?")[1]).get("subject") ?? "";
        expect(subject).toBe("Обращение по РепортКоллектор КППК");
    });

    test("carries the letterhead in the body and leaves room to type", () => {
        const url = supportMailto("support@example.ru", "Сырьё");
        const body = new URLSearchParams(url.split("?")[1]).get("body") ?? "";
        expect(body).toStartWith(SUPPORT_BODY_INTRO);
        // Blank lines below the letterhead are where the person writes.
        expect(body.endsWith("\n\n\n")).toBe(true);
    });

    test("escapes characters that mail clients mangle", () => {
        const url = supportMailto("support@example.ru", "Сырьё (тест)");
        // No raw parentheses/apostrophes in the query — they are percent-encoded.
        expect(url).not.toContain("(");
        expect(decode(url.split("subject=")[1]!.split("&")[0]!)).toContain("(тест)");
    });
});
