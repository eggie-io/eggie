import type { UploadItem } from "./queue";

const PHRASE: Record<string, string> = {
  going: "going up",
  waiting: "waiting",
  paused: "paused",
  stalled: "stalled",
  noRoom: "stopped",
  failed: "didn't make it",
  done: "in",
};

export function summary(items: readonly UploadItem[]): string {
  const counts = new Map<string, number>();
  for (const item of items) counts.set(item.state, (counts.get(item.state) ?? 0) + 1);
  return [...counts].map(([state, n]) => `${n} ${PHRASE[state]}`).join(" · ");
}

export function into(dir: string): string {
  return dir === "" ? "into the top of the project" : `into ${dir}/`;
}

export function failure(item: UploadItem): string {
  switch (item.reason) {
    case "file_exists":
      return `${item.name} is already in ${item.dir === "" ? "the top of the project" : `${item.dir}/`}. Replace it?`;
    case "permission_denied":
      return "Eggie can't write into that folder — pick another.";
    case "project_not_found":
      return "The project is gone.";
    case "path_is_folder":
      return "There's a folder with that name already.";
    case "upload_not_found":
      return "Eggie lost this upload — send it again.";
    default:
      return item.message ?? "Something went wrong.";
  }
}
