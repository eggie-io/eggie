import { describe, expect, it } from "vitest";
import { nextBaseline, updateView, type RuntimeUpdate } from "./update";

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

  it("the update is done once the installed release moved from the baseline", () => {
    expect(updateView(status({ installed: "0.3.0", available: null }), "0.2.0")).toEqual({ kind: "done" });
  });

  it("keeps the overlay while running, even if the release already reads as new", () => {
    expect(updateView(status({ state: "running", installed: "0.3.0" }), "0.2.0")).toEqual({ kind: "updating" });
  });

  it("an unknown installed release never counts as done", () => {
    expect(updateView(status({ state: "failed", installed: null, error: "boom" }), "0.2.0")).toMatchObject({
      kind: "offer", problem: "boom",
    });
    expect(updateView(status({ state: "idle", installed: null }), "0.2.0")).not.toEqual({ kind: "done" });
  });

  it("a failed update this tab watched goes back to the offer, not done", () => {
    expect(updateView(status({ state: "failed", error: "boom" }), "0.2.0")).toMatchObject({
      kind: "offer", problem: "boom",
    });
  });
});

describe("baseline across an update", () => {
  const watch = (answers: RuntimeUpdate[]) => {
    let baseline: string | null = null;
    return answers.map((answer) => {
      baseline = nextBaseline(baseline, answer.installed);
      return updateView(answer, baseline).kind;
    });
  };

  it("a tab that first saw the update with installed unknown still reloads once the release moved", () => {
    expect(watch([
      status({ installed: "0.1.0" }),
      status({ installed: null, state: "running" }),
      status({ installed: "0.2.0", available: null }),
    ])).toEqual(["offer", "updating", "done"]);
  });

  it("a tab opened mid-update reloads after it", () => {
    // The first release it sees is already the new one; the page is current, so no reload.
    expect(watch([
      status({ installed: null, state: "running" }),
      status({ installed: "0.2.0", available: null }),
    ])).toEqual(["updating", "hidden"]);
  });
});
