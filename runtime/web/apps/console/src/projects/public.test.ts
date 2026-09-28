import { describe, expect, it } from "vitest";
import { EXPIRED_NOTE, publicView, tileLine, timeLeft } from "./public";
import type { PublicStatus } from "./types";

const at = (sec: number) => sec * 1000;
const on: PublicStatus = {
  state: "on",
  urls: [{ url: "https://k3x9.example.dev", service: "web", local_url: "http://b" }],
  expires_at: 10_000,
};

describe("publicView", () => {
  it("flips an on URL to off with the expired note the moment it runs out", () => {
    expect(publicView(on, at(9_999)).kind).toBe("on");
    expect(publicView(on, at(10_000))).toEqual({ kind: "off", note: EXPIRED_NOTE });
  });

  it("keeps a URL with no time limit on, with no countdown", () => {
    const urls = [{ url: "https://k3x9.example.dev", service: "web", local_url: "http://b" }];
    const view = publicView({ state: "on", urls, expires_at: null }, at(10_000_000));
    expect(view).toEqual({ kind: "on", urls, left: null });
    expect(tileLine(view)).toBe("On");
  });

  it("carries the API's own words for every other state", () => {
    const reason = { code: "no_web", message: "This project has no web page to share." };
    expect(publicView({ state: "unavailable", reason }, 0)).toEqual({ kind: "unavailable", message: reason.message });
    expect(publicView({ state: "failed", reason }, 0)).toEqual({ kind: "failed", message: reason.message });
    expect(publicView({ state: "off", note: reason }, 0)).toEqual({ kind: "off", note: reason.message });
  });
});

describe("timeLeft", () => {
  it("counts down in minutes, then hours and minutes, never below a minute", () => {
    expect(timeLeft(10_000, at(10_000 - 42 * 60))).toBe("42 min left");
    expect(timeLeft(10_000, at(10_000 - 65 * 60))).toBe("1 h 5 min left");
    expect(timeLeft(10_000, at(10_000 - 20))).toBe("under a minute left");
  });
});

describe("tileLine", () => {
  it("says what the tile is doing in a couple of words", () => {
    expect(tileLine(publicView(on, at(10_000 - 42 * 60)))).toBe("42 min left");
    expect(tileLine({ kind: "enabling" })).toBe("Turning on…");
    expect(tileLine({ kind: "failed", message: "x" })).toBe("Didn't work");
    expect(tileLine({ kind: "off", note: null })).toBe("Off");
  });
});
