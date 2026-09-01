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
