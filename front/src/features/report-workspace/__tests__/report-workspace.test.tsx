import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * `ReportWorkspace` is the only place that glues the three projects together:
 * it picks the workspace by URL key, mirrors the chosen form into `?section=`
 * and switches projects by pushing the new slug. A mistake here is invisible to
 * every other test — the workspaces themselves are covered separately — so the
 * routing contract is pinned here.
 */

const replaceCalls: string[] = [];
const pushCalls: string[] = [];
const router = {
    replace: (url: string) => replaceCalls.push(url),
    push: (url: string) => pushCalls.push(url),
};

mock.module("next/navigation", () => ({ useRouter: () => router }));

// The workspaces are heavy and own their own data; here they only need to prove
// WHICH one was rendered and expose the callback the shell wires for them.
mock.module("@/features/raw-materials/raw-materials-workspace", () => ({
    RawMaterialsWorkspace: (props: Record<string, unknown>) => (
        <button
            data-testid="raw"
            data-project={String(props.activeProjectKey)}
            onClick={() =>
                (props.onSectionChange as (section: string | null) => void)("materialMovement")
            }
        >
            raw
        </button>
    ),
    Brand: () => <span>brand</span>,
}));
mock.module("@/features/renovation/renovation-workspace", () => ({
    RenovationWorkspace: (props: Record<string, unknown>) => (
        <button
            data-testid="renovation"
            data-project={String(props.activeProjectKey)}
            data-section={String(props.initialSection)}
            onClick={() => (props.onSectionChange as (section: string | null) => void)(null)}
        >
            renovation
        </button>
    ),
}));
mock.module("@/features/kppk/kppk-workspace", () => ({
    KppkWorkspace: (props: Record<string, unknown>) => (
        <button
            data-testid="kppk"
            onClick={() => (props.onProjectChange as (key: string) => void)("renovation")}
        >
            kppk
        </button>
    ),
}));
mock.module("@/features/logistics/logistics-workspace", () => ({
    LogisticsWorkspace: (props: Record<string, unknown>) => (
        <button
            data-testid="logistics"
            data-project={String(props.activeProjectKey)}
            data-section={String(props.initialSection)}
        >
            logistics
        </button>
    ),
}));

const { ReportWorkspace } = await import("../report-workspace");

const user = {
    email: "admin@example.ru",
    name: "Admin User",
    roles: [],
    access: [],
} as never;

const originalLocation = window.location;

beforeEach(() => {
    replaceCalls.length = 0;
    pushCalls.length = 0;
    Object.defineProperty(window, "location", {
        configurable: true,
        value: { ...originalLocation, search: "" },
    });
});

afterEach(() => {
    cleanup();
    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
});

describe("report workspace routing", () => {
    test("an unknown or missing user renders an empty shell, not a workspace", () => {
        render(
            <ReportWorkspace currentUser={null as never} projectKey="renovation" supportMail="" />,
        );
        expect(screen.queryByTestId("renovation")).toBeNull();
        expect(screen.queryByTestId("raw")).toBeNull();
    });

    test("the URL key picks the matching project", async () => {
        // The workspaces are `next/dynamic`: each loads only for its own URL, so
        // the assertion waits for that chunk instead of assuming it is inline.
        render(<ReportWorkspace currentUser={user} projectKey="renovation" supportMail="" />);
        expect((await screen.findByTestId("renovation")).dataset.project).toBe("renovation");
        cleanup();

        render(<ReportWorkspace currentUser={user} projectKey="kppk" supportMail="" />);
        expect(await screen.findByTestId("kppk")).toBeTruthy();
        cleanup();

        render(<ReportWorkspace currentUser={user} projectKey="logistics" supportMail="" />);
        expect((await screen.findByTestId("logistics")).dataset.project).toBe("logistics");
        cleanup();

        render(<ReportWorkspace currentUser={user} projectKey="raw_materials" supportMail="" />);
        expect((await screen.findByTestId("raw")).dataset.project).toBe("raw_materials");
    });

    test("an unrecognised project key falls back to raw materials", async () => {
        render(<ReportWorkspace currentUser={user} projectKey="something-else" supportMail="" />);
        expect(await screen.findByTestId("raw")).toBeTruthy();
    });

    test("the form is mirrored into ?section= with replace, not push", async () => {
        render(<ReportWorkspace currentUser={user} projectKey="raw_materials" supportMail="" />);
        fireEvent.click(await screen.findByTestId("raw"));

        expect(replaceCalls).toEqual(["/raw-materials?section=materialMovement"]);
        // Back must still leave the page, not step through every tab.
        expect(pushCalls).toEqual([]);
    });

    test("an empty form removes ?section= instead of writing an empty value", async () => {
        Object.defineProperty(window, "location", {
            configurable: true,
            value: { ...originalLocation, search: "?section=materialMovement&block=БСЗ" },
        });
        render(<ReportWorkspace currentUser={user} projectKey="renovation" supportMail="" />);
        fireEvent.click(await screen.findByTestId("renovation"));

        // The other query parameters survive: only `section` is dropped.
        expect(replaceCalls).toEqual(["/renovation?block=%D0%91%D0%A1%D0%97"]);
    });

    test("the restored section is handed to the workspace", async () => {
        render(
            <ReportWorkspace
                currentUser={user}
                projectKey="renovation"
                initialSection="kppkCost"
                supportMail=""
            />,
        );
        expect((await screen.findByTestId("renovation")).dataset.section).toBe("kppkCost");
    });

    test("switching projects pushes the slug and drops ?section=", async () => {
        Object.defineProperty(window, "location", {
            configurable: true,
            value: { ...originalLocation, search: "?section=kppkPositions" },
        });
        render(<ReportWorkspace currentUser={user} projectKey="kppk" supportMail="" />);
        fireEvent.click(await screen.findByTestId("kppk"));

        // Form ids differ per project, so the old one must not travel along.
        expect(pushCalls).toEqual(["/renovation"]);
        expect(replaceCalls).toEqual([]);
    });
});
