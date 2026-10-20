/**
 * URL slug ↔ project key mapping.
 *
 * The backend identifies projects by key (raw_materials / renovation / kppk),
 * the URLs use the hyphenated slug. A project page provisions the visitor
 * into that project (an inactive membership the admin can activate); the root
 * URL never provisions anything.
 */

const SLUG_TO_KEY: Record<string, string> = {
    "raw-materials": "raw_materials",
    renovation: "renovation",
    kppk: "kppk",
};

export function projectKeyFromSlug(slug: string): string | null {
    return SLUG_TO_KEY[slug] ?? null;
}

export function projectSlugFromKey(key: string): string | null {
    const slug = Object.entries(SLUG_TO_KEY).find(([, projectKey]) => projectKey === key);
    return slug ? `/${slug[0]}` : null;
}

/**
 * Human-readable project names, mirroring the backend constants
 * (`RAW_MATERIALS_PROJECT_NAME` / `RENOVATION_PROJECT_NAME` /
 * `KPPK_PROJECT_NAME`). Needed where the user's access list is unavailable —
 * e.g. the «no access to the project» page — and for the support letter's
 * subject.
 */
export const PROJECT_NAMES: Record<string, string> = {
    raw_materials: "Сырьё",
    renovation: "Ремонт",
    kppk: "КППК",
};

/** The display name of a project key («Сырьё» / «Ремонт» / «КППК»). */
export function projectNameFromKey(key: string): string {
    return PROJECT_NAMES[key] ?? key;
}
