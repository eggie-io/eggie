import { describe, expect, it } from "vitest";
import { latchStart, updateView, type RuntimeUpdate } from "./update";

const status = (over: Partial<RuntimeUpdate> = {}): RuntimeUpdate => ({
  installed: "0.2.0", available: "0.3.0", state: "idle", error: null, ...over,
});

describe("updateView", () => {
  it("is hidden while nothing newer exists", () => {
    expect(updateView(status({ available: null }), null)).toEqual({ kind: "hidden" });
  });

  it("is hidden before the first answer", () => {
    expect(updateView(undefined, null)).toEqual({ kind: "hidden" });
  });

  it("offers the available release with no problem", () => {
    expect(updateView(status(), null)).toEqual({
      kind: "offer", available: "0.3.0", installed: "0.2.0", problem: null,
    });
  });

  it("a failed attempt still offers, with its error", () => {
    expect(updateView(status({ state: "failed", error: "pull failed" }), null)).toMatchObject({
      kind: "offer", problem: "pull failed",
    });
    expect(updateView(status({ state: "failed" }), null)).toMatchObject({ problem: "no reason given" });
  });

  it("a running update shows the overlay even when this tab did not start it", () => {
    expect(updateView(status({ state: "running" }), null)).toEqual({ kind: "updating" });
  });

  it("the update is done once the installed release moved from where it started", () => {
    expect(updateView(status({ installed: "0.3.0", available: null }), "0.2.0")).toEqual({ kind: "done" });
  });

  it("keeps the overlay until the new release is reported, not just until running ends", () => {
    expect(updateView(status({ state: "running", installed: "0.3.0" }), "0.2.0")).toEqual({ kind: "updating" });
  });

  it("a failed update this tab started goes back to the offer, not done", () => {
    expect(updateView(status({ state: "failed", error: "boom" }), "0.2.0")).toMatchObject({
      kind: "offer", problem: "boom",
    });
  });
});

describe("latchStart", () => {
  it("a tab that only saw the update running still reloads once the release moved", () => {
    const running = status({ state: "running" });
    const from = latchStart(updateView(running, null), null, running.installed);
    expect(from).toBe("0.2.0");
    expect(updateView(status({ installed: "0.3.0", available: null }), from)).toEqual({ kind: "done" });
  });

  it("keeps the release it first latched", () => {
    expect(latchStart({ kind: "updating" }, "0.2.0", "0.3.0")).toBe("0.2.0");
  });

  it("does not latch outside an update", () => {
    expect(latchStart({ kind: "hidden" }, null, "0.2.0")).toBeNull();
  });
});
