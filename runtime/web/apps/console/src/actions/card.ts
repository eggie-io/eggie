import { ApiError } from "../api/client";
import type { Action, ActionsAnswer, Asking, ConnectionAnswer, ConnectionInfo, ConnectionState, Run } from "./types";

export type Card =
  | { kind: "loading" }
  | { kind: "unavailable"; message: string }
  | { kind: "unknown"; id: string }
  | { kind: "action"; action: Action };

const BLOCKING = ["cloud_unavailable", "cloud_error", "invalid_project_yml", "connections_unavailable"];

export function cardFor(answer: ActionsAnswer | undefined, error: unknown, id: string): Card {
  if (!answer) {
    if (error === undefined || error === null) return { kind: "loading" };
    return { kind: "unavailable", message: error instanceof ApiError ? error.message : "Actions aren't available right now." };
  }
  const action = answer.actions.find((a) => a.id === id);
  if (action) return { kind: "action", action };
  // Dropped for one of its connections: the API names the action on that problem.
  const own = answer.problems.find((p) => p.action === id);
  if (own) return { kind: "unavailable", message: own.message };
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

export type ConnectionView =
  | { kind: "checking" | "connected" | "signed-out" | "unknown" | "getting-link" | "failed" | "timed-out" }
  | { kind: "waiting"; link: string; code: string | null }
  | { kind: "asking"; asking: Asking };

export function connectionView(answer: ConnectionAnswer | undefined, startedHere: boolean): ConnectionView {
  if (!answer) return { kind: "checking" };
  const { run, state } = answer;
  if (run?.state === "running") {
    if (run.asking) return { kind: "asking", asking: run.asking };
    if (run.link) return { kind: "waiting", link: run.link, code: run.code };
    return { kind: "getting-link" };
  }
  // An old failed login shouldn't haunt the card; only the one this card started reports.
  if (startedHere && run?.state === "exited" && state !== "connected" && state !== "checking") {
    if (run.timed_out) return { kind: "timed-out" };
    // No exit code without the deadline: the owner pressed Cancel.
    if (run.exit_code !== null && (run.exit_code !== 0 || state === "signed-out")) return { kind: "failed" };
  }
  return { kind: state };
}

export function gate(
  action: Action,
  info: ConnectionInfo[],
  states: Record<string, ConnectionState | undefined>,
): string | null {
  for (const id of action.connections) {
    const known = info.find((c) => c.id === id);
    if (known?.login && states[id] === "signed-out") return `Connect ${known.name} first`;
  }
  return null;
}
