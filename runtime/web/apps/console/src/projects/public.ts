import type { PublicStatus, PublicUrl } from "./types";

// The API's own wording for its `expired` note; needed here because the
// countdown reaches zero before the next refetch does.
export const EXPIRED_NOTE = "The public address expired. Start a new one; it will be a different address.";

export type PublicView =
  | { kind: "unavailable"; message: string }
  | { kind: "off"; note: string | null }
  | { kind: "enabling" }
  | { kind: "on"; urls: PublicUrl[]; left: string | null }
  | { kind: "failed"; message: string };

export function timeLeft(expiresAtSec: number, nowMs: number): string {
  const minutes = Math.floor((expiresAtSec - nowMs / 1000) / 60);
  if (minutes < 1) return "under a minute left";
  if (minutes < 60) return `${minutes} min left`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min left`;
}

export function publicView(status: PublicStatus, nowMs: number): PublicView {
  switch (status.state) {
    case "on":
      // A null expiry means the service set no time limit.
      if (status.expires_at === null) return { kind: "on", urls: status.urls, left: null };
      return status.expires_at * 1000 <= nowMs
        ? { kind: "off", note: EXPIRED_NOTE }
        : { kind: "on", urls: status.urls, left: timeLeft(status.expires_at, nowMs) };
    case "off":
      return { kind: "off", note: status.note?.message ?? null };
    case "enabling":
      return { kind: "enabling" };
    case "unavailable":
      return { kind: "unavailable", message: status.reason.message };
    case "failed":
      return { kind: "failed", message: status.reason.message };
  }
}

export function tileLine(view: PublicView): string {
  switch (view.kind) {
    case "on":
      return view.left ?? "On";
    case "enabling":
      return "Turning on…";
    case "failed":
      return "Didn't work";
    case "unavailable":
      return view.message;
    case "off":
      return "Off";
  }
}
