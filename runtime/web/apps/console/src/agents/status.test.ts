import { describe, expect, it } from "vitest";
import { statusLine } from "./status";

describe("statusLine", () => {
  it("says connected even when an earlier setup failed", () => {
    expect(statusLine("Codex", { connected: true, setup: "failed" }).kind).toBe("connected");
  });

  it("shows setup progress and failure before waiting", () => {
    expect(statusLine("Codex", { connected: false, setup: "installing" }).kind).toBe("installing");
    expect(statusLine("Codex", { connected: false, setup: "failed" }).kind).toBe("failed");
  });

  it("waits when the runner has not reported this agent yet", () => {
    expect(statusLine("Codex", undefined)).toEqual({ kind: "waiting", text: "Waiting for Codex to connect…" });
    expect(statusLine("Codex", { connected: false, setup: "ready" }).kind).toBe("waiting");
  });
});
