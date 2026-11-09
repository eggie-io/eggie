import { describe, expect, it } from "vitest";
import { polling, type GitHubStatus, type Repo, type SetupState } from "./github";
import { filterRepos, githubView, repoRow, shouldLoadMore } from "./view";

const connected = (setup: SetupState, setup_error: string | null = null): GitHubStatus =>
  ({ state: "connected", login: "octo", name: "Octo", email: "e", setup, setup_error }) as const;

describe("githubView", () => {
  it("offers a plain connect when nothing has happened", () => {
    expect(githubView({ state: "disconnected", error: null })).toEqual({
      kind: "connect", problem: null, action: "Connect GitHub",
    });
  });

  it.each([
    ["access_denied", "Try again"],
    ["expired_token", "Get a new code"],
    ["github_error", "Try again"],
    ["something_new", "Try again"],
  ])("explains %s and offers a way back", (error, action) => {
    const view = githubView({ state: "disconnected", error });
    expect(view).toMatchObject({ kind: "connect", action });
    expect(view.kind === "connect" && view.problem).toBeTruthy();
  });

  it("shows the code while pending", () => {
    expect(githubView({ state: "pending", user_code: "AB-CD", url: "u", expires_at: 5 })).toEqual({
      kind: "code", code: "AB-CD", expiresAt: 5,
    });
  });

  it("is only ready once the machine has applied it", () => {
    expect(githubView(connected("applying")).kind).toBe("applying");
    expect(githubView(connected("ready"))).toEqual({ kind: "ready", login: "octo" });
  });

  it("says a runtime update is needed without offering a pointless retry", () => {
    const view = githubView(connected("runtime_outdated"));
    expect(view).toMatchObject({ kind: "setupFailed", canRetry: false });
    expect(view.kind === "setupFailed" && view.message).toMatch(/update/);
  });

  it("names a timeout and a script failure differently, both retryable", () => {
    const timeout = githubView(connected("failed", "setup_timeout"));
    const failed = githubView(connected("failed", "root: gh broke"));
    expect(timeout).toMatchObject({ kind: "setupFailed", canRetry: true });
    expect(failed).toMatchObject({ kind: "setupFailed", canRetry: true });
    expect(failed.kind === "setupFailed" && failed.message).toContain("root: gh broke");
    expect(timeout.kind === "setupFailed" && timeout.message).not.toContain("setup_timeout");
  });

  it("asks for a reconnect when the token stopped working", () => {
    expect(githubView({ state: "needs_reconnect", login: "octo" })).toEqual({ kind: "reconnect", login: "octo" });
  });
});

describe("polling", () => {
  it("polls only while something is about to change", () => {
    expect(polling({ state: "pending", user_code: "x", url: "u", expires_at: 1 })).toBe(true);
    expect(polling(connected("applying"))).toBe(true);
    expect(polling(connected("ready"))).toBe(false);
    expect(polling({ state: "disconnected", error: null })).toBe(false);
    expect(polling(undefined)).toBe(false);
  });
});

describe("repoRow", () => {
  const repo = (over: Partial<Repo>): Repo => ({
    full_name: "octo/app", private: false, description: null, updated_at: null,
    project: null, name_taken: false, suggested_id: "app", ...over,
  });

  it("opens the existing project even when its name is also taken", () => {
    expect(repoRow(repo({ project: "renamed", name_taken: true, suggested_id: "app-2" })))
      .toEqual({ kind: "linked", projectId: "renamed" });
  });

  it("asks for another name when the default one is taken", () => {
    expect(repoRow(repo({ name_taken: true, suggested_id: "app-2" })))
      .toEqual({ kind: "nameTaken", suggestion: "app-2" });
  });

  it("clones directly when the name is free or has no usable suggestion", () => {
    expect(repoRow(repo({}))).toEqual({ kind: "free" });
    expect(repoRow(repo({ name_taken: true, suggested_id: null }))).toEqual({ kind: "free" });
  });
});

describe("filterRepos", () => {
  const named = (full_name: string): Repo => ({
    full_name, private: false, description: null, updated_at: null,
    project: null, name_taken: false, suggested_id: null,
  });
  const repos = ["ada/Recipe-Box", "ada/garden-log", "kitchen-co/tiny-crm"].map(named);
  const names = (query: string) => filterRepos(repos, query).map((repo) => repo.full_name);

  it("matches any part of the owner/name, ignoring case", () => {
    expect(names("RECIPE")).toEqual(["ada/Recipe-Box"]);
    expect(names("ada/")).toEqual(["ada/Recipe-Box", "ada/garden-log"]);
  });

  it("ignores surrounding spaces", () => {
    expect(names("  tiny ")).toEqual(["kitchen-co/tiny-crm"]);
  });

  it("keeps every repo for an empty or blank query", () => {
    expect(names("")).toHaveLength(3);
    expect(names("   ")).toHaveLength(3);
  });
});

describe("shouldLoadMore", () => {
  it("keeps fetching pages while a query is set and more remain", () => {
    expect(shouldLoadMore("crm", true, false)).toBe(true);
  });

  it("leaves paging to Load more without a query", () => {
    expect(shouldLoadMore("", true, false)).toBe(false);
    expect(shouldLoadMore("  ", true, false)).toBe(false);
  });

  it("waits for the page in flight", () => {
    expect(shouldLoadMore("crm", true, true)).toBe(false);
  });

  it("stops once every page is loaded", () => {
    expect(shouldLoadMore("crm", false, false)).toBe(false);
  });
});
