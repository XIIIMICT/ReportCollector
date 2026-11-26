import type { Page } from "@playwright/test";

import { loginAsLocalUser } from "./auth";

/**
 * Local-auth e2e identities. These addresses are reserved test accounts
 * provisioned by the isolated CI seed (`RC_SEED_E2E_FIXTURES=1`); they are never
 * corporate users and are not created by any runtime API. See root
 * `.env.example` for the env contract and local provisioning.
 */

export type TestUserId = "admin" | "assetWorkerSnhz" | "assetWorkerBoth" | "assetWorkerVos";

export interface TestUserCredentials {
    email: string;
    password: string;
}

interface TestUserIdentity {
    email: string;
    name: string;
}

/** Distinct people so the collaboration presence assertions see four members. */
export const TEST_USERS: Readonly<Record<TestUserId, TestUserIdentity>> = {
    admin: { email: "user1@test.local", name: "Евтушенко Сергей Николаевич" },
    assetWorkerSnhz: { email: "user2@test.local", name: "Смирнов Иван Викторович" },
    assetWorkerBoth: { email: "user3@test.local", name: "Петров Дмитрий Анатольевич" },
    assetWorkerVos: { email: "user4@test.local", name: "Лебедева Ольга Ивановна" },
};

const PASSWORD_ENV = "E2E_TEST_PASSWORD";

/** Fail loudly and early: a missing password must never turn into a skip. */
export function testPassword(): string {
    const password = process.env[PASSWORD_ENV];
    if (!password) {
        throw new Error(
            `${PASSWORD_ENV} is not set. Export the isolated stand's test-user password before running e2e.`,
        );
    }
    return password;
}

export function testUserCredentials(id: TestUserId): TestUserCredentials {
    return { email: TEST_USERS[id].email, password: testPassword() };
}

/** Local-auth login only: no Keycloak redirect or SSO credentials involved. */
export async function loginAsTestUser(page: Page, id: TestUserId) {
    await loginAsLocalUser(page, testUserCredentials(id));
}
