import type { ProjectState } from "@eggie/ui";
import type { ActiveJob, Project } from "./types";

export type Cause = "bound_to_loopback" | "service_unreachable" | "crash_looping" | "failed_to_start" | "unreadable";

// Who wrote a fault message: Docker, or Eggie when it could not read the project.
export type Source = "docker" | "eggie";

export type ProjectView =
  | { kind: "starting"; badge: ProjectState; job: ActiveJob }
  | { kind: "stopping"; badge: ProjectState; job: ActiveJob }
  | { kind: "gone" | "running" | "stopped"; badge: ProjectState }
  | { kind: "waiting"; badge: "waiting" }
  | { kind: "setup"; badge: "setup"; detail: string | null; source: Source }
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
  const fault = wrong(project);
  if (!project.ran_ok) {
    if (project.empty && project.origin === "new") return { kind: "waiting", badge: "waiting" };
    // An empty folder's compose_missing is Eggie's, not something Docker said.
    if (project.empty) return { kind: "setup", badge: "setup", detail: null, source: "eggie" };
    if (fault) {
      const source = fault.kind === "wrong" && fault.cause === "unreadable" ? "eggie" : "docker";
      return { kind: "setup", badge: "setup", detail: project.problem?.message ?? null, source };
    }
  }
  if (project.empty) return { kind: "waiting", badge: "waiting" };
  if (fault) return fault;
  if (project.status === "started_ok") return { kind: "running", badge: "running" };
  return { kind: "stopped", badge: "stopped" };
}
