// Must match the API's _slug: every run of characters outside [a-z0-9]
// (dashes included) becomes one dash.
export function slugify(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}
