import { afterEach, expect, test } from "bun:test";

/**
 * The rule behind the grids' outside-click selection clear.
 *
 * revo-grid drops its own focus rectangle when the pointer leaves for the page
 * and emits NOTHING for it, so the footer status and the collaboration presence
 * kept showing a selection that no longer existed (`Del` already did nothing).
 *
 * BOTH grids implement it, because they are different components: the plan
 * renders its own RevoGrid (`features/renovation/renovation-grid.tsx`) while
 * «Сырьё», КППК and the renovation dictionaries share
 * `shared/grid/data-grid.tsx`. Wiring it into only the shared one
 * left the plan broken — the first attempt at this fix did exactly that.
 */
type Verdict = "clear" | "keep";

// Mirrors `handleOutsidePointerDown` in both grids.
function outsideClickVerdict(target: Element, shell: Element): Verdict {
    if (target.closest('[data-slot="popover-dialog"], [role="dialog"], [role="alertdialog"]'))
        return "keep";
    return shell.contains(target) ? "keep" : "clear";
}

function mountShell(): HTMLElement {
    const shell = document.createElement("div");
    shell.className = "revo-grid-shell";
    const cell = document.createElement("div");
    cell.className = "rgCell";
    shell.appendChild(cell);
    document.body.appendChild(shell);
    return shell;
}

afterEach(() => {
    document.body.innerHTML = "";
});

test("a click on a grid cell keeps the selection", () => {
    const shell = mountShell();
    expect(outsideClickVerdict(shell.querySelector(".rgCell")!, shell)).toBe("keep");
});

test("a click elsewhere on the page clears it", () => {
    const shell = mountShell();
    const footer = document.createElement("div");
    document.body.appendChild(footer);
    expect(outsideClickVerdict(footer, shell)).toBe("clear");
});

test("a click in a HeroUI popover portal keeps it", () => {
    // The header-filter popover and the smart select render into <body>, far
    // from the shell — clearing here would drop the range being filtered.
    const shell = mountShell();
    const popover = document.createElement("div");
    popover.setAttribute("data-slot", "popover-dialog");
    const item = document.createElement("button");
    popover.appendChild(item);
    document.body.appendChild(popover);
    expect(outsideClickVerdict(item, shell)).toBe("keep");
});

test("a click in a dialog portal keeps it", () => {
    const shell = mountShell();
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "alertdialog");
    const button = document.createElement("button");
    dialog.appendChild(button);
    document.body.appendChild(dialog);
    expect(outsideClickVerdict(button, shell)).toBe("keep");
});
