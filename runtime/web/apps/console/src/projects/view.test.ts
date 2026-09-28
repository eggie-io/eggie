import { describe, expect, it } from "vitest";
import type { ActiveJob, Project } from "./types";
import { projectView } from "./view";

function project(over: Partial<Project> = {}): Project {
  return {
    id: "recipe-box",
    status: "stopped",
    domain: "127-0-0-1.sslip.io",
    path: "/opt/omelet/projects/recipe-box",
    urls: [],
    problem: null,
    empty: false,
    web: [],
    first_run: false,
    job: null,
    public: { state: "off", note: null },
    ...over,
  };
}

const job = (kind: ActiveJob["kind"]): ActiveJob => ({ id: "j1", kind, phase: "preparing", started_at: 1 });
const problem = (code: string) => ({ code, message: `${code} message` });

describe("projectView", () => {
  it("shows a running up or restart job as starting, even over a stale problem", () => {
    for (const kind of ["up", "restart"] as const) {
      const view = projectView(project({ job: job(kind), problem: problem("bound_to_loopback") }));
      expect([view.kind, view.badge]).toEqual(["starting", "starting"]);
    }
  });

  it("shows a running down job as stopping with the stopped badge", () => {
    const view = projectView(project({ status: "started_ok", job: job("down") }));
    expect([view.kind, view.badge]).toEqual(["stopping", "stopped"]);
  });

  it("calls a project whose folder is gone 'gone', not a generic fault", () => {
    const view = projectView(project({ problem: problem("folder_missing") }));
    expect([view.kind, view.badge]).toEqual(["gone", "wrong"]);
  });

  it("treats an empty folder as waiting even though the API reports compose_missing", () => {
    const view = projectView(project({ empty: true, problem: problem("compose_missing") }));
    expect([view.kind, view.badge]).toEqual(["waiting", "stopped"]);
  });

  it("names the cause from the problem code before the status", () => {
    const view = projectView(project({ status: "failed_to_start", problem: problem("bound_to_loopback") }));
    expect(view).toEqual({ kind: "wrong", badge: "wrong", cause: "bound_to_loopback", detail: null });
  });

  it("falls back to the status when there is no problem", () => {
    for (const status of ["failed_to_start", "crash_looping"] as const) {
      expect(projectView(project({ status }))).toEqual({ kind: "wrong", badge: "wrong", cause: status, detail: null });
    }
  });

  it("groups files Omelet can't read under 'unreadable' and keeps the API's message", () => {
    for (const code of ["invalid_compose", "invalid_project", "compose_missing"]) {
      expect(projectView(project({ problem: problem(code) }))).toEqual({
        kind: "wrong",
        badge: "wrong",
        cause: "unreadable",
        detail: `${code} message`,
      });
    }
  });

  it("shows an unknown problem code as unreachable with the API's message", () => {
    expect(projectView(project({ status: "started_ok", problem: problem("brand_new_code") }))).toEqual({
      kind: "wrong",
      badge: "wrong",
      cause: "service_unreachable",
      detail: "brand_new_code message",
    });
  });

  it("is running only when started_ok with no problem", () => {
    const view = projectView(project({ status: "started_ok" }));
    expect([view.kind, view.badge]).toEqual(["running", "running"]);
  });

  it("is stopped otherwise", () => {
    const view = projectView(project());
    expect([view.kind, view.badge]).toEqual(["stopped", "stopped"]);
  });
});
