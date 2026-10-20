import { describe, expect, it } from "vitest";
import { analyzePrompt, newPrompt, setupPrompt } from "./prompts";

describe("newPrompt", () => {
  it("names the folder and tells the agent to start it with eggie up", () => {
    const prompt = newPrompt("my-app");
    expect(prompt).toContain("~/projects/my-app");
    expect(prompt).toContain("eggie up");
  });
});

describe("setupPrompt", () => {
  it("says where the code came from per origin", () => {
    expect(setupPrompt("a", "github", null)).toMatch(/^I copied ~\/projects\/a from GitHub into Eggie and/);
    expect(setupPrompt("a", "folder", null)).toMatch(/^I copied ~\/projects\/a from a folder into Eggie and/);
    expect(setupPrompt("a", "new", null)).toMatch(/^I copied ~\/projects\/a into Eggie and/);
  });

  it("appends Docker's message only when there is one", () => {
    expect(setupPrompt("a", "github", "exit code 1")).toMatch(/what you changed\.\n\nDocker said: exit code 1$/);
    expect(setupPrompt("a", "github", null)).not.toContain("Docker said");
  });
});

describe("analyzePrompt", () => {
  it("points the start question at the error and carries it", () => {
    const prompt = analyzePrompt("start", "a", "exit code 1");
    expect(prompt).toContain("the Dockerfile and the error below.");
    expect(prompt).toMatch(/go-ahead\.\n\nexit code 1$/);
  });

  it("drops the error from the start question when there is none", () => {
    const prompt = analyzePrompt("start", "a", null);
    expect(prompt).toContain("the Dockerfile. Don't change");
    expect(prompt).not.toContain("error below");
    expect(prompt).toMatch(/go-ahead\.$/);
  });
});
