import { afterEach, expect, test } from "bun:test";

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { useRef } from "react";

import { usePopoverDismiss } from "@/shared/hooks/use-popover-dismiss";

afterEach(cleanup);

test("owner closes only after the exit animation finishes", async () => {
    let finish!: () => void;
    const finished = new Promise<void>((resolve) => {
        finish = resolve;
    });
    let closes = 0;
    let reads = 0;
    function Harness() {
        const ref = useRef<HTMLDivElement>(null);
        const { isOpen, dismiss } = usePopoverDismiss(ref, () => {
            closes++;
        });
        return (
            <div
                ref={(node) => {
                    ref.current = node;
                    if (node)
                        node.getAnimations = () => {
                            reads++;
                            return [{ finished }] as unknown as Animation[];
                        };
                }}
                data-open={isOpen}
            >
                <button onClick={dismiss}>Close</button>
            </div>
        );
    }
    const view = render(<Harness />);
    fireEvent.click(view.getByRole("button"));
    await waitFor(() => expect(reads).toBe(1));
    expect(closes).toBe(0);
    await act(async () => {
        finish();
    });
    expect(closes).toBe(1);
});

test("without animation the owner closes on the next frame", async () => {
    let closes = 0;
    function Harness() {
        const ref = useRef<HTMLDivElement>(null);
        const { dismiss } = usePopoverDismiss(ref, () => {
            closes++;
        });
        return (
            <div ref={ref}>
                <button onClick={dismiss}>Close</button>
            </div>
        );
    }
    const view = render(<Harness />);
    fireEvent.click(view.getByRole("button"));
    expect(closes).toBe(0);
    await waitFor(() => expect(closes).toBe(1));
});
