import { expect, test } from "@playwright/test";

test("healthcheck отвечает 200", async ({ request }) => {
    const response = await request.get("/api/health");
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ status: "ok" });
});
