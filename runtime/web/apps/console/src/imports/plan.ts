import { slugify } from "../projects/slugify";

// What the browser hands over: a path that starts with the chosen folder's
// own name (webkitRelativePath, or a dropped entry's fullPath) and the file.
export interface Picked {
  path: string;
  file: File;
}

export interface Entry {
  path: string;
  size: number;
  file: Blob;
}

export interface ImportPlan {
  name: string;
  id: string;
  entries: Entry[];
  bytes: number;
  skipped: string[];
}

// The same two sets host/client.py applies for the CLI and install
// verification: a repository's object store, dependency trees and caches
// are megabytes the VM has no use for, and `.git/config` carries credentials.
export const EXCLUDED_DIRS = new Set([".git", "node_modules", ".venv", "__pycache__"]);
// The overlay is generated inside the VM on every start; `.eggie/project.yml`
// next to it is the user's own configuration and goes in.
export const EXCLUDED_FILES = new Set([".eggie/overlay.yml"]);

const BATCH = 500;
const nextTask = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

// The first `file.size` of a picked file is a blocking disk stat (about half
// a millisecond each in Chrome), so a big folder is planned in batches that
// give the page a chance to paint the count.
export async function planImport(
  picked: readonly Picked[],
  onProgress: (done: number, total: number) => void = () => {},
  pause: () => Promise<void> = nextTask,
): Promise<ImportPlan> {
  const entries: Entry[] = [];
  const skipped = new Set<string>();
  let name = "";
  let bytes = 0;
  for (let i = 0; i < picked.length; i += 1) {
    if (i > 0 && i % BATCH === 0) {
      onProgress(i, picked.length);
      await pause();
    }
    const { path, file } = picked[i];
    const parts = path.split("/").filter((part) => part !== "");
    if (parts.length < 2) continue;
    if (name === "") name = parts[0];
    const inside = parts.slice(1);
    const excluded = inside.find((part) => EXCLUDED_DIRS.has(part));
    if (excluded !== undefined) {
      skipped.add(excluded);
      continue;
    }
    const rel = inside.join("/");
    if (EXCLUDED_FILES.has(rel)) continue;
    entries.push({ path: rel, size: file.size, file });
    bytes += file.size;
  }
  onProgress(picked.length, picked.length);
  return { name, id: slugify(name), entries, bytes, skipped: [...skipped].sort() };
}
