import { afterEach, expect, test } from "bun:test";

import type { EditorBase, EditorType, HyperFunc, VNode } from "@revolist/react-datagrid";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { useEffect } from "react";

import { reactEditor } from "../react-editor";

afterEach(cleanup);

test("editor root teardown is deferred and idempotent during parent unmount", async () => {
    let unmounts = 0;
    function Editor() {
        useEffect(
            () => () => {
                unmounts++;
            },
            [],
        );
        return <span>Editor content</span>;
    }
    const column = {
        prop: "value",
        model: { value: "" },
        column: { prop: "value" },
    } as unknown as EditorType["column"];
    const Adapter = reactEditor(Editor) as new (
        column: EditorType["column"],
        save: EditorType["save"],
        close: EditorType["close"],
    ) => EditorBase;
    const editor = new Adapter(
        column,
        () => {},
        () => {},
    );
    let attach: ((element: HTMLElement | null) => void) | undefined;
    editor.render(((_tag: string, props: { ref: typeof attach }) => {
        attach = props.ref;
        return {} as VNode;
    }) as HyperFunc<VNode>);
    const errors: string[] = [];
    const originalError = console.error;
    console.error = (...args) => {
        errors.push(args.map(String).join(" "));
    };
    try {
        const view = render(<div ref={(element) => attach?.(element)} />);
        await waitFor(() => expect(view.container.textContent).toBe("Editor content"));
        await act(async () => {
            view.unmount();
            editor.disconnectedCallback?.();
            // Cleanup cannot synchronously unmount another root in this commit.
            expect(unmounts).toBe(0);
        });
        expect(unmounts).toBe(1);
        expect(errors.filter((error) => error.includes("synchronously unmount"))).toEqual([]);
    } finally {
        console.error = originalError;
    }
});
