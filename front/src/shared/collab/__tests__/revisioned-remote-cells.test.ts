import { expect, test } from "bun:test";

import {
    applyRevisionedRemoteCell,
    reconcileRevisionedRemoteCells,
} from "../revisioned-remote-cells";

test("older websocket revisions cannot overwrite newer values", () => {
    let cells = applyRevisionedRemoteCell(
        new Map(),
        "table/r/f",
        {
            value: "new",
            revision: 4,
            at: "2999-10-28T10:00:02.000Z",
        },
        null,
    );
    cells = applyRevisionedRemoteCell(
        cells,
        "table/r/f",
        {
            value: "old",
            revision: 3,
            at: "2999-10-28T10:00:03.000Z",
        },
        null,
    );
    expect(cells.get("table/r/f")?.value).toBe("new");
});

test("an authoritative read removes overlays that predate its request", () => {
    let cells = applyRevisionedRemoteCell(
        new Map(),
        "table/r/f",
        {
            value: "before",
            revision: 1,
            at: "2000-10-28T10:00:00.000Z",
        },
        null,
    );
    cells = reconcileRevisionedRemoteCells(cells, Date.now() + 1);
    expect(cells.size).toBe(0);
});

test("events received during a read survive that read", () => {
    const cells = applyRevisionedRemoteCell(
        new Map(),
        "table/r/f",
        {
            value: "after",
            revision: 2,
            at: "2999-10-28T10:00:02.000Z",
        },
        Date.now() - 1,
    );
    expect(reconcileRevisionedRemoteCells(cells, Date.now() - 1).get("table/r/f")?.value).toBe(
        "after",
    );
});
