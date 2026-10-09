import { EXCLUDED_DIRS, type Picked } from "./plan";

// A folder chosen with <input webkitdirectory>: every file carries its path
// from the chosen folder down, folder name first.
export function fromFileList(files: FileList): Picked[] {
  return Array.from(files).map((file) => ({ path: file.webkitRelativePath, file }));
}

function fileOf(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

// readEntries hands back batches (Chrome: 100 at a time) until an empty one.
async function entriesOf(dir: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
  const reader = dir.createReader();
  const all: FileSystemEntry[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (batch.length === 0) return all;
    all.push(...batch);
  }
}

// Excluded folders are never opened: a dependency tree can hold most of a
// project's files, and the planner would drop them anyway. The folder's
// name is still recorded under the path so the plan reports it as left out.
async function walk(dir: FileSystemDirectoryEntry, into: Picked[], onFound: (count: number) => void): Promise<void> {
  const children = await entriesOf(dir);
  const files = children.filter((child): child is FileSystemFileEntry => child.isFile);
  const picked = await Promise.all(files.map(async (entry) => ({ path: entry.fullPath, file: await fileOf(entry) })));
  into.push(...picked);
  onFound(into.length);
  for (const child of children) {
    if (!child.isDirectory) continue;
    if (EXCLUDED_DIRS.has(child.name)) {
      into.push({ path: `${child.fullPath}/`, file: new File([], "") });
      continue;
    }
    await walk(child as FileSystemDirectoryEntry, into, onFound);
  }
}

// The first folder among the dropped items, walked; null when nothing
// dropped was a folder. Only the entry API tells a folder from a file.
export async function fromDrop(items: DataTransferItemList, onFound: (count: number) => void = () => {}): Promise<Picked[] | null> {
  for (const item of Array.from(items)) {
    const entry = item.kind === "file" ? item.webkitGetAsEntry() : null;
    if (entry?.isDirectory) {
      const picked: Picked[] = [];
      await walk(entry as FileSystemDirectoryEntry, picked, onFound);
      return picked;
    }
  }
  return null;
}
