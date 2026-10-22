import type { EditorCtr, EditorType, HyperFunc, VNode } from "@revolist/react-datagrid";
import { createElement, type ComponentType } from "react";
import { createRoot, type Root } from "react-dom/client";

/** React editor bridge with teardown outside the parent React commit. */
export function reactEditor(Component: ComponentType<EditorType>): EditorCtr {
    return function (column, save, close) {
        let host: HTMLElement | null = null;
        let mount: HTMLElement | null = null;
        let root: Root | null = null;
        const dispose = () => {
            // Clear ownership synchronously: detached roots must never be
            // reused, and ref(null) + disconnectedCallback may both run.
            const detached = root;
            root = null;
            host = null;
            // A Stencil ref may reuse its host before the deferred unmount.
            // Give every root its own mount so old cleanup cannot erase new UI.
            mount?.remove();
            mount = null;
            if (detached) queueMicrotask(() => detached.unmount());
        };
        return {
            column,
            disconnectedCallback: dispose,
            render(h: HyperFunc<VNode>) {
                return h("span", {
                    key: `${column.prop}-${this.editCell?.rowIndex ?? 0}`,
                    ref: (element: HTMLElement | null) => {
                        if (!element) {
                            dispose();
                            return;
                        }
                        if (element !== host) {
                            dispose();
                            host = element;
                            mount = document.createElement("div");
                            mount.style.display = "contents";
                            element.append(mount);
                            root = createRoot(mount);
                        }
                        root?.render(
                            createElement(Component, {
                                ...this.editCell,
                                column,
                                save,
                                close,
                            }),
                        );
                    },
                });
            },
        };
    };
}
