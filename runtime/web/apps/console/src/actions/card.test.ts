import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { cardFor, connectionView, gate, runStatus } from "./card";
import type { Action, ActionsAnswer, ConnectionAnswer, ConnectionInfo, ConnectionRun, ConnectionState } from "./types";

const build: Action = {
  id: "ios-build", recipe: "expo", name: "Build for iPhone", description: null,
  command: "eas build", type: "one-off", run: null, connections: [], asks: false,
};

describe("cardFor", () => {
  it("shows a loading card until the first answer", () => {
    expect(cardFor(undefined, null, "ios-build")).toEqual({ kind: "loading" });
  });

  it("surfaces a failed request as unavailable with the API's message", () => {
    const error = new ApiError("actions_unavailable", "Actions are not available right now", 503);
    expect(cardFor(undefined, error, "ios-build")).toEqual({ kind: "unavailable", message: "Actions are not available right now" });
  });

  it("an id the answer lacks is unknown", () => {
    const answer: ActionsAnswer = { actions: [build], problems: [], connections: [] };
    expect(cardFor(answer, null, "nope")).toEqual({ kind: "unknown", id: "nope" });
  });

  it("an unreachable service with no such action is unavailable", () => {
    const answer: ActionsAnswer = {
      actions: [],
      problems: [{ recipe: null, code: "cloud_unavailable", message: "Can't reach Eggie's service" }],
      connections: [],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "unavailable", message: "Can't reach Eggie's service" });
  });

  it("a service error with no such action is unavailable, not a missing action", () => {
    const answer: ActionsAnswer = {
      actions: [],
      problems: [{ recipe: "expo", code: "cloud_error", message: "Eggie's service had a problem" }],
      connections: [],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "unavailable", message: "Eggie's service had a problem" });
  });

  it("an unreadable project.yml shows its problem, not a missing action", () => {
    const answer: ActionsAnswer = {
      actions: [],
      problems: [{ recipe: null, code: "invalid_project_yml", message: "project.yml is not valid YAML" }],
      connections: [],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "unavailable", message: "project.yml is not valid YAML" });
  });

  it("a matching action is shown even when another recipe has a problem", () => {
    const answer: ActionsAnswer = {
      actions: [build],
      problems: [{ recipe: "stripe", code: "recipe_not_found", message: "No recipe stripe" }],
      connections: [],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "action", action: build });
  });
});

describe("runStatus", () => {
  it("a run that exited with no code was stopped, not failed", () => {
    expect(runStatus({ state: "exited", exit_code: null, started_at: 1, timed_out: false })).toEqual({ tone: "neutral", text: "Stopped" });
  });

  it("a non-zero exit is a failure with its code", () => {
    expect(runStatus({ state: "exited", exit_code: 2, started_at: 1, timed_out: false })).toEqual({ tone: "danger", text: "Exited 2" });
  });
});

const run = (over: Partial<ConnectionRun>): ConnectionRun => ({
  state: "running", exit_code: null, started_at: 1, timed_out: false, link: null, code: null, asking: null, ...over,
});
const conn = (state: ConnectionState, r: ConnectionRun | null): ConnectionAnswer => ({ id: "expo", name: "Expo", state, run: r });

describe("connectionView", () => {
  it("no answer yet reads as checking", () => {
    expect(connectionView(undefined, false)).toEqual({ kind: "checking" });
  });

  it("a running login walks from getting the link to waiting to asking", () => {
    expect(connectionView(conn("signed-out", run({})), true)).toEqual({ kind: "getting-link" });
    expect(connectionView(conn("signed-out", run({ link: "https://x/y", code: "AB-12" })), true))
      .toEqual({ kind: "waiting", link: "https://x/y", code: "AB-12" });
    const asking = { source: "expo", id: "otp", label: "Code", type: "text" as const };
    expect(connectionView(conn("signed-out", run({ link: "https://x/y", asking })), false)).toEqual({ kind: "asking", asking });
  });

  it("a login that exited 0 and is now connected is connected", () => {
    expect(connectionView(conn("connected", run({ state: "exited", exit_code: 0 })), true)).toEqual({ kind: "connected" });
  });

  it("a login this card started that exited 0 but is still signed out failed", () => {
    expect(connectionView(conn("signed-out", run({ state: "exited", exit_code: 0 })), true)).toEqual({ kind: "failed" });
  });

  it("a login this card started that exited 1 and is signed out failed", () => {
    expect(connectionView(conn("signed-out", run({ state: "exited", exit_code: 1 })), true)).toEqual({ kind: "failed" });
  });

  it("a login this card started and then cancelled shows the plain state, not a failure", () => {
    expect(connectionView(conn("signed-out", run({ state: "exited", exit_code: null })), true)).toEqual({ kind: "signed-out" });
  });

  it("an old failed login shows the plain state", () => {
    expect(connectionView(conn("signed-out", run({ state: "exited", exit_code: 1 })), false)).toEqual({ kind: "signed-out" });
  });

  it("a login this card started that hit the deadline timed out", () => {
    expect(connectionView(conn("signed-out", run({ state: "exited", exit_code: null, timed_out: true })), true))
      .toEqual({ kind: "timed-out" });
    expect(connectionView(conn("signed-out", run({ state: "exited", exit_code: null, timed_out: true })), false))
      .toEqual({ kind: "signed-out" });
  });
});

describe("gate", () => {
  const info: ConnectionInfo[] = [
    { id: "expo", name: "Expo", description: null, login: true, check: true },
    { id: "apple", name: "Apple", description: null, login: true, check: true },
  ];
  const act = { ...build, connections: ["expo", "apple"] };

  it("blocks on the first signed-out connection by name", () => {
    expect(gate(act, info, { expo: "connected", apple: "signed-out" })).toBe("Connect Apple first");
  });

  it("checking, unknown and missing states never block", () => {
    expect(gate(act, info, { expo: "checking", apple: "unknown" })).toBeNull();
    expect(gate(act, info, {})).toBeNull();
  });
});
