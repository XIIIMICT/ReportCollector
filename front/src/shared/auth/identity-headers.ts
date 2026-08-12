import { createHmac } from "node:crypto";

import type { getSession } from "./session";
import { getSessionConfig } from "./session";

export function buildIdentityHeaders(session: Awaited<ReturnType<typeof getSession>>): Headers {
    const headers = new Headers();

    if (!session) {
        headers.set("X-ReportCollector-User-Key", "local.user");
        return headers;
    }

    const timestamp = Math.floor(Date.now() / 1000).toString();
    const values = [
        session.preferredUsername ?? session.email ?? session.sub,
        session.email,
        session.name,
        session.sub,
        session.issuer,
        session.preferredUsername,
        session.givenName,
        session.familyName,
        session.emailVerified === undefined ? undefined : String(session.emailVerified),
    ];
    const names = [
        "X-ReportCollector-User-Key",
        "X-ReportCollector-User-Email",
        "X-ReportCollector-User-Name",
        "X-ReportCollector-User-Sub",
        "X-ReportCollector-User-Issuer",
        "X-ReportCollector-Preferred-Username",
        "X-ReportCollector-Given-Name",
        "X-ReportCollector-Family-Name",
        "X-ReportCollector-Email-Verified",
    ];
    names.forEach((name, index) => setIdentityHeader(headers, name, values[index]));

    const payload = [timestamp, ...values.map((value) => value ?? "")].join("\n");
    const signature = createHmac("sha256", getSessionConfig().secret).update(payload).digest("hex");
    headers.set("X-ReportCollector-Identity-Timestamp", timestamp);
    headers.set("X-ReportCollector-Identity-Signature", signature);

    return headers;
}

function setIdentityHeader(headers: Headers, name: string, value: string | undefined) {
    if (value !== undefined) {
        headers.set(name, encodeURIComponent(value));
    }
}
