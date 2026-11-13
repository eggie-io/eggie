import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { cardFor, runStatus } from "./card";
import type { Action, ActionsAnswer } from "./types";

const build: Action = {
  id: "ios-build", recipe: "expo", name: "Build for iPhone", description: null,
  command: "eas build", type: "one-off", run: null,
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
    const answer: ActionsAnswer = { actions: [build], problems: [] };
    expect(cardFor(answer, null, "nope")).toEqual({ kind: "unknown", id: "nope" });
  });

  it("an unreachable service with no such action is unavailable", () => {
    const answer: ActionsAnswer = {
      actions: [],
      problems: [{ recipe: null, code: "cloud_unavailable", message: "Can't reach Eggie's service" }],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "unavailable", message: "Can't reach Eggie's service" });
  });

  it("a service error with no such action is unavailable, not a missing action", () => {
    const answer: ActionsAnswer = {
      actions: [],
      problems: [{ recipe: "expo", code: "cloud_error", message: "Eggie's service had a problem" }],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "unavailable", message: "Eggie's service had a problem" });
  });

  it("an unreadable project.yml shows its problem, not a missing action", () => {
    const answer: ActionsAnswer = {
      actions: [],
      problems: [{ recipe: null, code: "invalid_project_yml", message: "project.yml is not valid YAML" }],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "unavailable", message: "project.yml is not valid YAML" });
  });

  it("a matching action is shown even when another recipe has a problem", () => {
    const answer: ActionsAnswer = {
      actions: [build],
      problems: [{ recipe: "stripe", code: "recipe_not_found", message: "No recipe stripe" }],
    };
    expect(cardFor(answer, null, "ios-build")).toEqual({ kind: "action", action: build });
  });
});

describe("runStatus", () => {
  it("a run that exited with no code was stopped, not failed", () => {
    expect(runStatus({ state: "exited", exit_code: null, started_at: 1 })).toEqual({ tone: "neutral", text: "Stopped" });
  });

  it("a non-zero exit is a failure with its code", () => {
    expect(runStatus({ state: "exited", exit_code: 2, started_at: 1 })).toEqual({ tone: "danger", text: "Exited 2" });
  });
});
