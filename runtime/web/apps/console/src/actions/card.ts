import { ApiError } from "../api/client";
import type { Action, ActionsAnswer } from "./types";

export type Card =
  | { kind: "loading" }
  | { kind: "unavailable"; message: string }
  | { kind: "unknown"; id: string }
  | { kind: "action"; action: Action };

export function cardFor(answer: ActionsAnswer | undefined, error: unknown, id: string): Card {
  if (!answer) {
    if (error === undefined || error === null) return { kind: "loading" };
    return { kind: "unavailable", message: error instanceof ApiError ? error.message : "Actions aren't available right now." };
  }
  const action = answer.actions.find((a) => a.id === id);
  if (action) return { kind: "action", action };
  // The recipe that would define it could not be fetched, so "no such action" would be a guess.
  const cloud = answer.problems.find((p) => p.code === "cloud_unavailable");
  if (cloud) return { kind: "unavailable", message: cloud.message };
  return { kind: "unknown", id };
}
