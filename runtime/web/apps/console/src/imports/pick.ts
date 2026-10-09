import type { Picked } from "./plan";

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

async function walk(entry: FileSystemEntry, into: Picked[]): Promise<void> {
  if (entry.isFile) {
    into.push({ path: entry.fullPath, file: await fileOf(entry as FileSystemFileEntry) });
  } else if (entry.isDirectory) {
    for (const child of await entriesOf(entry as FileSystemDirectoryEntry)) await walk(child, into);
  }
}

// The first folder among the dropped items, walked; null when nothing
// dropped was a folder. Only the entry API tells a folder from a file.
export async function fromDrop(items: DataTransferItemList): Promise<Picked[] | null> {
  for (const item of Array.from(items)) {
    const entry = item.kind === "file" ? item.webkitGetAsEntry() : null;
    if (entry?.isDirectory) {
      const picked: Picked[] = [];
      await walk(entry, picked);
      return picked;
    }
  }
  return null;
}
