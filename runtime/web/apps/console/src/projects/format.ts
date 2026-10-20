import type { DeletePreview } from "./types";

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];

function counted(n: number): string {
  return n < WORDS.length ? WORDS[n] : String(n);
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function elapsed(startedAtSec: number, nowMs: number): string {
  const total = Math.max(0, Math.floor(nowMs / 1000 - startedAtSec));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

export function subtitle(total: number, cooking: number, waiting = 0, agent = "your coding agent"): string {
  if (total === 0) return "Nothing on the go yet";
  const head = `${counted(total)} on the go`;
  if (waiting > 0) return `${head} · ${counted(waiting).toLowerCase()} waiting on ${agent}`;
  return cooking === 0 ? head : `${head} · ${counted(cooking).toLowerCase()} of them cooking`;
}

export function folderHeading(count: number): string {
  return count === 1 ? "A folder turned up" : `${counted(count)} folders turned up`;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function size(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const shown = unit > 0 && value < 10 ? value.toFixed(1).replace(/\.0$/, "") : String(Math.round(value));
  return `${shown} ${UNITS[unit]}`;
}

export function relativeTime(atSec: number, nowMs: number): string {
  const seconds = Math.max(0, nowMs / 1000 - atSec);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${plural(Math.floor(seconds / 60), "minute")} ago`;
  if (seconds < 86_400) return `${plural(Math.floor(seconds / 3600), "hour")} ago`;
  return `${plural(Math.floor(seconds / 86_400), "day")} ago`;
}

export function hostOf(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

export interface BinRow {
  label: string;
  detail: string;
  names?: string[];
}

export function deleteRows(preview: DeletePreview): BinRow[] {
  const rows: BinRow[] = [];
  if (preview.files > 0) {
    rows.push({ label: "Every file in the project", detail: `${plural(preview.files, "file")} · ${size(preview.bytes)}` });
  }
  if (preview.containers.length > 0) {
    rows.push({ label: "The little machines that run it", detail: "stopped & removed", names: preview.containers });
  }
  if (preview.volumes.length > 0) {
    rows.push({ label: "Its stored data", detail: "deleted", names: preview.volumes });
  }
  return rows;
}
