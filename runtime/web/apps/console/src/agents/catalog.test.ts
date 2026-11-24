import { describe, expect, it } from "vitest";
import { loadCatalog, parseAgent, parseIndex } from "./catalog";

const guide = (over = {}) => ({
  tagline: "SSH connection",
  card: [{ label: "Host", value: "{user}@{host}" }],
  steps: [{ title: "Open it", body: "Sign in.", media: ["mac/1.png"], alt: "Start screen" }],
  ...over,
});

describe("parseAgent", () => {
  it("resolves icon and screenshots against the agent's folder", () => {
    const agent = parseAgent("claude-code", { name: "Claude Code", icon: "icon.svg", platforms: { cloud: guide() } }, "/agents");
    expect(agent?.icon).toBe("/agents/claude-code/icon.svg");
    expect(agent?.guide?.steps[0].media).toEqual([{ kind: "image", src: "/agents/claude-code/mac/1.png" }]);
  });

  it("takes several screenshots and videos for one step, in order", () => {
    const steps = [{ title: "Open it", body: "Sign in.", media: ["mac/1.png", "mac/2.webp", "mac/3.mp4"], alt: "Start screen" }];
    const agent = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ steps }) } }, "/agents");
    expect(agent?.guide?.steps[0].media).toEqual([
      { kind: "image", src: "/agents/codex/mac/1.png" },
      { kind: "image", src: "/agents/codex/mac/2.webp" },
      { kind: "video", src: "/agents/codex/mac/3.mp4" },
    ]);
  });

  it("drops a step whose media is not a list, or names a file it can't show", () => {
    const step = (media: unknown) => ({ title: "Open it", body: "Sign in.", media, alt: "Start screen" });
    for (const media of ["mac/1.png", ["mac/1.pdf"], ["../x.png"]]) {
      expect(parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ steps: [step(media)] }) } }, "/a")).toBeNull();
    }
  });

  it("keeps a step without media as a placeholder", () => {
    const steps = [{ title: "Open it", body: "Sign in.", alt: "Start screen" }];
    const agent = parseAgent("codex", { name: "Codex", icon: "icon.svg", platforms: { cloud: guide({ steps }) } }, "/agents");
    expect(agent?.guide?.steps[0].media).toEqual([]);
  });

  it("parses the body as HTML and drops a step whose HTML it doesn't allow", () => {
    const step = (body: string) => ({ title: "Open it", body, alt: "Start screen" });
    const ok = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ steps: [step("<ul><li>Host</li></ul>")] }) } }, "/a");
    expect(ok?.guide?.steps[0].body).toEqual([{ tag: "ul", children: [{ tag: "li", children: ["Host"] }] }]);
    const bad = parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ steps: [step("<img src=x>")] }) } }, "/a");
    expect(bad).toBeNull();
  });

  it("parses a guide's warning as HTML, and drops the guide when it isn't allowed", () => {
    const agent = (warning: unknown) => parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ warning }) } }, "/a");
    expect(agent("Run <code>wsl</code> first.")?.guide?.warning).toEqual(["Run ", { tag: "code", children: ["wsl"] }, " first."]);
    expect(agent(undefined)?.guide?.warning).toBeNull();
    for (const bad of ["", "<a href=x>x</a>", 42]) expect(agent(bad)).toBeNull();
  });

  it("reads the card's fields, and a guide may have no card", () => {
    const agent = (card: unknown) => parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ card }) } }, "/a");
    expect(agent([{ label: "Host", value: "{user}@{host}" }])?.guide?.card).toEqual([{ label: "Host", value: "{user}@{host}" }]);
    expect(agent(undefined)?.guide?.card).toBeNull();
  });

  it("drops a guide whose card names a fact the API does not give", () => {
    const card = [{ label: "Host", value: "{username}@{host}" }];
    expect(parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ card }) } }, "/a")).toBeNull();
    expect(parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ card: [] }) } }, "/a")).toBeNull();
  });

  it("reads the note, and drops an agent whose note is not text", () => {
    const agent = (note: unknown) => parseAgent("codex", { name: "Codex", note, icon: "i.svg", platforms: { cloud: guide() } }, "/a");
    expect(agent("(in ChatGPT)")?.note).toBe("(in ChatGPT)");
    expect(agent(undefined)?.note).toBeNull();
    for (const bad of ["", " ", 42]) expect(agent(bad)).toBeNull();
  });

  it("drops an agent that is not an object, such as the SPA's index.html", () => {
    expect(parseAgent("codex", "<!doctype html>", "/agents")).toBeNull();
  });

  it("knows whether an agent has a setup, without exposing the command", () => {
    const withSetup = parseAgent("codex", { name: "Codex", icon: "i.svg", setup: { run: "curl x | sh" }, platforms: { cloud: guide() } }, "/a");
    const without = parseAgent("cursor", { name: "Cursor", icon: "i.svg", platforms: { cloud: guide() } }, "/a");
    expect(withSetup?.hasSetup).toBe(true);
    expect(without?.hasSetup).toBe(false);
  });

  it("drops an agent whose guide has no steps", () => {
    expect(parseAgent("codex", { name: "Codex", icon: "icon.svg", platforms: { cloud: guide({ steps: [] }) } }, "/agents")).toBeNull();
  });

  it("reads only the cloud guide, whatever else the manifest carries", () => {
    const only = (platforms: object) => parseAgent("codex", { name: "Codex", icon: "i.svg", platforms }, "/agents");
    expect(only({ mac: guide({ tagline: "Mac" }), windows: guide(), cloud: guide({ tagline: "Cloud" }) })?.guide?.tagline).toBe("Cloud");
    expect(only({ mac: guide() })?.guide).toBeNull();
  });

  it("reads a card field's copy mark, which can only be true", () => {
    const agent = (field: unknown) => parseAgent("codex", { name: "Codex", icon: "i.svg", platforms: { cloud: guide({ card: [field] }) } }, "/a");
    expect(agent({ label: "Folder", value: "C:\\x", copy: true })?.guide?.card).toEqual([{ label: "Folder", value: "C:\\x", copy: true }]);
    expect(agent({ label: "Folder", value: "C:\\x" })?.guide?.card).toEqual([{ label: "Folder", value: "C:\\x" }]);
    expect(agent({ label: "Folder", value: "C:\\x", copy: false })).toBeNull();
  });

  it("refuses a path that climbs out of the agent's folder", () => {
    expect(parseAgent("codex", { name: "Codex", icon: "../../x.svg", platforms: { cloud: guide() } }, "/agents")).toBeNull();
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

describe("loadCatalog", () => {
  it("keeps the good agents when one fails to load", async () => {
    const files: Record<string, unknown> = {
      "/agent-guides/index.json": { agents: ["claude-code", "codex"] },
      "/agent-guides/claude-code/agent.json": { name: "Claude Code", icon: "icon.svg", platforms: { cloud: guide() } },
    };
    const fetchJson = async (url: string) => {
      if (!(url in files)) throw new Error("404");
      return files[url];
    };
    expect((await loadCatalog(fetchJson)).map((a) => a.id)).toEqual(["claude-code"]);
  });
});
