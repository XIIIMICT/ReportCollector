import type { GridViewStats } from "./grid-types";

/**
 * Have the footer's grid statistics really changed?
 *
 * Two workspaces published a fresh stats object on every grid event, which
 * re-rendered the workspace (and therefore the grid) in a loop. Both used the
 * same content check inline; keeping it in one place stops one copy drifting
 * into a comparison that accepts every publication.
 *
 * The check is intentionally the existing JSON one: stats are plain data (no
 * functions, no maps), so a structural string comparison is enough. `null`
 * means "nothing published yet" and is never equal to a real publication.
 */
export function sameGridViewStats(current: GridViewStats | null, next: GridViewStats): boolean {
    return Boolean(current) && JSON.stringify(current) === JSON.stringify(next);
}
