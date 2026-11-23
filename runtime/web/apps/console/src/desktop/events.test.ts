import { afterEach, describe, expect, it, vi } from "vitest";
import { reportEvent, setEventContext, setEventPoster, takeDesktopEvents } from "./events";

const SID = "5b0c2f8e-3f0a-4b8e-9d4c-1f2e3a4b5c6d";
const CTX = { app_version: "0.9.1", os: "windows" as const, os_version: "10.0.26100", arch: "AMD64" };

afterEach(() => {
  setEventContext(null);
  setEventPoster(null);
});

describe("takeDesktopEvents", () => {
  it("reads a valid context from the desktop link", () => {
    expect(takeDesktopEvents({ hash: `#handoff=x&sid=${SID}&app=0.9.1&os=windows&osv=10.0.26100&arch=AMD64` }))
      .toEqual({ sessionId: SID, context: CTX });
  });

  it("refuses a crafted or partial link", () => {
    expect(takeDesktopEvents({ hash: "#sid=../../x&app=1&os=linux&osv=1&arch=x" })).toBeNull();
    expect(takeDesktopEvents({ hash: "#handoff=x" })).toBeNull();
    expect(takeDesktopEvents({ hash: `#sid=${SID.toUpperCase()}&app=1&os=macos&osv=1&arch=x` })).toBeNull();
    expect(takeDesktopEvents({ hash: `#sid=${SID}&app=1&os=macos&osv=1` })).toBeNull();
  });
});

describe("reportEvent", () => {
  it("sends nothing without a context", () => {
    const post = vi.fn();
    setEventPoster(post);
    reportEvent("signup_screen_shown");
    expect(post).not.toHaveBeenCalled();
  });

  it("posts the contract body with the method", async () => {
    const post = vi.fn().mockResolvedValue(undefined);
    setEventPoster(post);
    setEventContext({ sessionId: SID, context: CTX });
    reportEvent("signup_started", { method: "new" });
    reportEvent("signup_screen_shown");
    expect(post.mock.calls).toEqual([
      [{ event: "signup_started", session_id: SID, properties: { method: "new" }, context: CTX }],
      [{ event: "signup_screen_shown", session_id: SID, properties: {}, context: CTX }],
    ]);
  });

  it("swallows a failed post", async () => {
    setEventPoster(() => Promise.reject(new Error("offline")));
    setEventContext({ sessionId: SID, context: CTX });
    expect(() => reportEvent("signup_screen_shown")).not.toThrow();
    await Promise.resolve();
  });
});
