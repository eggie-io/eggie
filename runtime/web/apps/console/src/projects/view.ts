import type { ProjectState } from "@eggie/ui";
import type { ActiveJob, Project } from "./types";

export type Cause = "bound_to_loopback" | "service_unreachable" | "crash_looping" | "failed_to_start" | "unreadable";

export type ProjectView =
  | { kind: "starting"; badge: ProjectState; job: ActiveJob }
  | { kind: "stopping"; badge: ProjectState; job: ActiveJob }
  | { kind: "gone" | "waiting" | "running" | "stopped"; badge: ProjectState }
  | { kind: "wrong"; badge: "wrong"; cause: Cause; detail: string | null };

const NAMED: Record<string, Cause> = {
  bound_to_loopback: "bound_to_loopback",
  service_unreachable: "service_unreachable",
  crash_looping: "crash_looping",
  failed_to_start: "failed_to_start",
};

const UNREADABLE = new Set(["invalid_compose", "invalid_project", "compose_missing"]);

function wrong(project: Project): ProjectView | null {
  const code = project.problem?.code ?? (project.status === "failed_to_start" || project.status === "crash_looping" ? project.status : null);
  if (code === null) return null;
  const message = project.problem?.message ?? null;
  if (UNREADABLE.has(code)) return { kind: "wrong", badge: "wrong", cause: "unreadable", detail: message };
  if (Object.hasOwn(NAMED, code)) return { kind: "wrong", badge: "wrong", cause: NAMED[code], detail: null };
  return { kind: "wrong", badge: "wrong", cause: "service_unreachable", detail: message };
}

export function projectView(project: Project): ProjectView {
  const job = project.job;
  if (job && (job.kind === "up" || job.kind === "restart" || job.kind === "clone")) return { kind: "starting", badge: "starting", job };
  if (job && job.kind === "down") return { kind: "stopping", badge: "stopped", job };
  if (project.problem?.code === "folder_missing") return { kind: "gone", badge: "wrong" };
  if (project.empty) return { kind: "waiting", badge: "stopped" };
  const fault = wrong(project);
  if (fault) return fault;
  if (project.status === "started_ok") return { kind: "running", badge: "running" };
  return { kind: "stopped", badge: "stopped" };
}
