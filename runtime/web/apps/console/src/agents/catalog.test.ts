import { describe, expect, it } from "vitest";
import { loadCatalog, parseAgent, parseIndex, platformFor } from "./catalog";

const guide = (over = {}) => ({
  tagline: "SSH connection",
  ssh: [{ label: "Host", value: "{user}@{host}" }],
  steps: [{ title: "Open it", body: "Sign in.", media: ["mac/1.png"], alt: "Start screen" }],
  ...over,
});

describe("parseAgent", () => {
  it("resolves icon and screenshots against the agent's folder", () => {
    const agent = parseAgent("claude-code", { name: "Claude Code", icon: "icon.svg", platforms: { mac: guide() } }, "/agents");
    expect(agent?.icon).toBe("/agents/claude-code/icon.svg");
    expect(agent?.platforms.mac?.steps[0].media).toEqual([{ kind: "image", src: "/agents/claude-code/mac/1.png" }]);
  });

  it("takes several screenshots and videos for one step, in order", () => {
    const steps = [{ title: "Open it", body: "Sign in.", media: ["mac/1.png", "mac/2.webp", "mac/3.mp4"], alt: "Start screen" }];
    const agent = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { mac: guide({ steps }) } }, "/agents");
    expect(agent?.platforms.mac?.steps[0].media).toEqual([
      { kind: "image", src: "/agents/codex/mac/1.png" },
      { kind: "image", src: "/agents/codex/mac/2.webp" },
      { kind: "video", src: "/agents/codex/mac/3.mp4" },
    ]);
  });

  it("drops a step whose media is not a list, or names a file it can't show", () => {
    const step = (media: unknown) => ({ title: "Open it", body: "Sign in.", media, alt: "Start screen" });
    for (const media of ["mac/1.png", ["mac/1.pdf"], ["../x.png"]]) {
      expect(parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { mac: guide({ steps: [step(media)] }) } }, "/a")).toBeNull();
    }
  });

  it("keeps a step without media as a placeholder", () => {
    const steps = [{ title: "Open it", body: "Sign in.", alt: "Start screen" }];
    const agent = parseAgent("codex", { name: "Codex", icon: "icon.svg", platforms: { windows: guide({ steps }) } }, "/agents");
    expect(agent?.platforms.windows?.steps[0].media).toEqual([]);
  });

  it("parses the body as HTML and drops a step whose HTML it doesn't allow", () => {
    const step = (body: string) => ({ title: "Open it", body, alt: "Start screen" });
    const ok = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { mac: guide({ steps: [step("<ul><li>Host</li></ul>")] }) } }, "/a");
    expect(ok?.platforms.mac?.steps[0].body).toEqual([{ tag: "ul", children: [{ tag: "li", children: ["Host"] }] }]);
    const bad = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { mac: guide({ steps: [step("<img src=x>")] }) } }, "/a");
    expect(bad).toBeNull();
  });

  it("reads the SSH card's fields, and no card means no SSH", () => {
    const agent = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { mac: guide(), windows: guide({ ssh: undefined }) } }, "/a");
    expect(agent?.platforms.mac?.ssh).toEqual([{ label: "Host", value: "{user}@{host}" }]);
    expect(agent?.platforms.windows?.ssh).toBeNull();
  });

  it("drops a guide whose SSH card names a fact the API does not give", () => {
    const ssh = [{ label: "Host", value: "{username}@{host}" }];
    expect(parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { mac: guide({ ssh }) } }, "/a")).toBeNull();
    expect(parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { mac: guide({ ssh: [] }) } }, "/a")).toBeNull();
  });

  it("drops an agent that is not an object, such as the SPA's index.html", () => {
    expect(parseAgent("codex", "<!doctype html>", "/agents")).toBeNull();
  });

  it("knows whether an agent has a setup, without exposing the command", () => {
    const withSetup = parseAgent("codex", { name: "Codex", icon: "i.svg", setup: { run: "curl x | sh" }, platforms: { mac: guide() } }, "/a");
    const without = parseAgent("cursor", { name: "Cursor", icon: "i.svg", platforms: { mac: guide() } }, "/a");
    expect(withSetup?.hasSetup).toBe(true);
    expect(without?.hasSetup).toBe(false);
  });

  it("drops an agent whose guide has no steps", () => {
    expect(parseAgent("codex", { name: "Codex", icon: "icon.svg", platforms: { mac: guide({ steps: [] }) } }, "/agents")).toBeNull();
  });

  it("ignores platforms it does not know", () => {
    const agent = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { linux: guide(), mac: guide() } }, "/agents");
    expect(Object.keys(agent?.platforms ?? {})).toEqual(["mac"]);
  });

  it("refuses a path that climbs out of the agent's folder", () => {
    expect(parseAgent("codex", { name: "Codex", icon: "../../x.svg", platforms: { mac: guide() } }, "/agents")).toBeNull();
  });
});

describe("parseIndex", () => {
  it("returns the ids in order", () => {
    expect(parseIndex({ agents: ["claude-code", "codex"] })).toEqual(["claude-code", "codex"]);
  });
  it("throws on anything else", () => {
    expect(() => parseIndex("<!doctype html>")).toThrow();
    expect(() => parseIndex({ agents: ["../x"] })).toThrow();
  });
});

describe("platformFor", () => {
  it("trusts the VM over the browser", () => {
    expect(platformFor({ vm: "wsl", ssh: null }, "Macintosh")).toBe("windows");
    expect(platformFor({ vm: "lima", ssh: null }, "Windows NT")).toBe("mac");
  });
  it("falls back to the browser when the VM is unknown", () => {
    expect(platformFor({ vm: "other", ssh: null }, "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)")).toBe("mac");
    expect(platformFor(undefined, "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
  });
});

describe("loadCatalog", () => {
  it("keeps the good agents when one fails to load", async () => {
    const files: Record<string, unknown> = {
      "/agent-guides/index.json": { agents: ["claude-code", "codex"] },
      "/agent-guides/claude-code/agent.json": { name: "Claude Code", icon: "icon.svg", platforms: { mac: guide() } },
    };
    const fetchJson = async (url: string) => {
      if (!(url in files)) throw new Error("404");
      return files[url];
    };
    expect((await loadCatalog(fetchJson)).map((a) => a.id)).toEqual(["claude-code"]);
  });
});
