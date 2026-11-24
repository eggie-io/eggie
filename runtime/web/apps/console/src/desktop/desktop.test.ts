import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { browserLink, desktopHome, takeDesktopHome } from "./desktop";

const HOME = "http://127.0.0.1:53817/index.html";

function store(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (key: string) => data[key] ?? null,
    setItem: (key: string, value: string) => {
      data[key] = value;
    },
  };
}

const hash = (home: string) => ({ hash: `#handoff=abc&home=${encodeURIComponent(home)}` });

describe("takeDesktopHome", () => {
  it("takes the desktop page's address from the handoff link", () => {
    expect(takeDesktopHome(hash(HOME), store())).toBe(HOME);
  });

  it("remembers it across a reload, which drops the fragment", () => {
    const s = store();
    takeDesktopHome(hash(HOME), s);
    expect(takeDesktopHome({ hash: "" }, s)).toBe(HOME);
  });

  it("is null in a plain browser", () => {
    expect(takeDesktopHome({ hash: "#handoff=abc" }, store())).toBeNull();
  });

  // The link is attacker-shapeable; a Home button must never lead off the machine.
  // Ports are non-default so each case fails on its host, not on a missing port.
  it.each([
    ["another host", "http://evil.example:53817/"],
    ["a lookalike host", "http://127.0.0.1.evil.example:53817/"],
    ["userinfo hiding the real host", "http://127.0.0.1:53817@evil.example:53817/"],
    ["https", "https://127.0.0.1:53817/"],
    ["a script URL", "javascript:alert(1)"],
    ["no port", "http://127.0.0.1/"],
    ["the console itself", "http://localhost:39080/"],
  ])("refuses %s", (_, url) => {
    expect(takeDesktopHome(hash(url), store())).toBeNull();
  });

  it("refuses a bad address even when one was stored earlier", () => {
    expect(desktopHome(store({ "eggie.desktopHome": "http://evil.example:53817/" }))).toBeNull();
  });

  it("still works when storage is unavailable", () => {
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    };
    expect(takeDesktopHome(hash(HOME), broken)).toBe(HOME);
  });
});

describe("browserLink", () => {
  const ORIGIN = "http://localhost:39080";

  it("signs the browser in with a fresh code", async () => {
    const api = { post: async () => ({ code: "xyz" }) } as never;
    expect(await browserLink(api, ORIGIN)).toBe(`${ORIGIN}/#handoff=xyz`);
  });

  it("still opens the page when no code can be had", async () => {
    // An older API, or a session that just ran out: the browser shows its
    // own sign-in screen, which beats a button that does nothing.
    const api = {
      post: async () => {
        throw new ApiError("not_signed_in", "no", 401);
      },
    } as never;
    expect(await browserLink(api, ORIGIN)).toBe(ORIGIN);
  });
});
