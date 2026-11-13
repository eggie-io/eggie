import type { DirEntry } from "../uploads/queries";

export const DOCS_DIR = ".eggie/docs";

const MD = /\.md$/;

export function pageNames(entries: DirEntry[]): string[] {
  const names = entries.filter((e) => e.kind === "file" && MD.test(e.name)).map((e) => e.name.replace(MD, ""));
  return names.sort((a, b) => (a === "index" ? -1 : b === "index" ? 1 : a.localeCompare(b, undefined, { sensitivity: "base" })));
}

export type LinkTarget = { kind: "page"; page: string } | { kind: "external"; href: string } | { kind: "none" };

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

function decode(part: string): string | null {
  try {
    return decodeURIComponent(part);
  } catch {
    return null;
  }
}

export function resolveLink(href: string): LinkTarget {
  if (/^(https?|mailto):/i.test(href)) return { kind: "external", href };
  if (SCHEME.test(href)) return { kind: "none" };
  const path = decode(href.split("#")[0].replace(/^\.\//, ""));
  if (!path || path.includes("/") || !MD.test(path)) return { kind: "none" };
  return { kind: "page", page: path.replace(MD, "") };
}

export function resolveImage(href: string): string | null {
  if (SCHEME.test(href) || href.startsWith("/")) return null;
  const parts = DOCS_DIR.split("/");
  for (const raw of href.split(/[?#]/)[0].split("/")) {
    const part = decode(raw);
    if (part === null || part.includes("/")) return null;
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (parts.length === 0) return null;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.length ? parts.join("/") : null;
}

export function docsRoute(projectId: string, page?: string): string {
  const base = `/p/${encodeURIComponent(projectId)}/docs`;
  return page === undefined ? base : `${base}/${encodeURIComponent(page)}`;
}
