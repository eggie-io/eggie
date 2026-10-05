import { describe, expect, it } from "vitest";
import { decide, readMark, writeMark } from "./onboarding";

const none = { agents: {} };
const connected = { agents: { codex: { connected: false, setup: null }, cursor: { connected: true, setup: null } } };

describe("decide", () => {
  it("onboards only a fresh kitchen: nothing connected, nothing on the counter", () => {
    expect(decide({ marked: false, statuses: none, counterItems: 0 })).toBe("onboarding");
  });

  it("never onboards once this browser finished or skipped it", () => {
    expect(decide({ marked: true, statuses: none, counterItems: 0 })).toBe("off");
  });

  it("skips when any agent is connected, without waiting for projects", () => {
    expect(decide({ marked: false, statuses: connected, counterItems: undefined })).toBe("off");
  });

  it("skips when there are projects, without waiting for agents", () => {
    expect(decide({ marked: false, statuses: undefined, counterItems: 2 })).toBe("off");
  });

  it("opens the app normally when either check fails", () => {
    expect(decide({ marked: false, statuses: null, counterItems: 0 })).toBe("off");
    expect(decide({ marked: false, statuses: none, counterItems: null })).toBe("off");
  });

  it("keeps deciding until both answers are in", () => {
    expect(decide({ marked: false, statuses: none, counterItems: undefined })).toBe("deciding");
    expect(decide({ marked: false, statuses: undefined, counterItems: 0 })).toBe("deciding");
  });
});

describe("mark", () => {
  it("round-trips through storage", () => {
    const data = new Map<string, string>();
    const store = () => ({ getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) }) as unknown as Storage;
    expect(readMark(store)).toBe(false);
    writeMark(store);
    expect(readMark(store)).toBe(true);
  });

  it("treats storage that throws as unmarked instead of crashing", () => {
    const store = () => {
      throw new DOMException("denied", "SecurityError");
    };
    expect(readMark(store)).toBe(false);
    expect(() => writeMark(store)).not.toThrow();
  });
});
