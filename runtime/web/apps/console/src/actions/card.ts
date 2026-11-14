import { ApiError } from "../api/client";
import type { Action, ActionsAnswer, Run } from "./types";

export type Card =
  | { kind: "loading" }
  | { kind: "unavailable"; message: string }
  | { kind: "unknown"; id: string }
  | { kind: "action"; action: Action };

const BLOCKING = ["cloud_unavailable", "cloud_error", "invalid_project_yml"];

export function cardFor(answer: ActionsAnswer | undefined, error: unknown, id: string): Card {
  if (!answer) {
    if (error === undefined || error === null) return { kind: "loading" };
    return { kind: "unavailable", message: error instanceof ApiError ? error.message : "Actions aren't available right now." };
  }
  const action = answer.actions.find((a) => a.id === id);
  if (action) return { kind: "action", action };
  // The recipes that would define it could not be read or fetched, so "no such action" would be a guess.
  const blocked = answer.problems.find((p) => BLOCKING.includes(p.code));
  if (blocked) return { kind: "unavailable", message: blocked.message };
  return { kind: "unknown", id };
}

export function runStatus(run: Run | null): { tone: "success" | "neutral" | "danger"; text: string } {
  if (run?.state === "running") return { tone: "success", text: "Running" };
  // No exit code: Stop ended it by signal.
  if (run?.state === "exited" && run.exit_code === null) return { tone: "neutral", text: "Stopped" };
  if (run?.state === "exited") return { tone: run.exit_code === 0 ? "neutral" : "danger", text: `Exited ${run.exit_code}` };
  return { tone: "neutral", text: "Stopped" };
}
