import { describe, expect, it } from "vitest";
import type { Agent } from "./catalog";
import { currentAgent } from "./current";

const agent = (id: string, name: string): Agent => ({ id, name, icon: `/agent-guides/${id}/icon.webp`, platforms: {}, hasSetup: false });
const AGENTS = [agent("claude-code", "Claude Code"), agent("codex", "Codex"), agent("cursor", "Cursor")];
const FALLBACK = { name: "your coding agent", connected: false, id: null };

describe("currentAgent", () => {
  it("picks the first connected agent in catalog order, not status order", () => {
    const statuses = { agents: { cursor: { connected: true, setup: null }, codex: { connected: true, setup: null } } };
    expect(currentAgent(AGENTS, statuses)).toEqual({ name: "Codex", connected: true, id: "codex" });
  });

  it("falls back when no agent is connected", () => {
    const statuses = { agents: { codex: { connected: false, setup: "installing" as const } } };
    expect(currentAgent(AGENTS, statuses)).toEqual(FALLBACK);
  });

  it("falls back while the statuses or the catalog are still loading", () => {
    expect(currentAgent(AGENTS, undefined)).toEqual(FALLBACK);
    expect(currentAgent(undefined, { agents: { codex: { connected: true, setup: null } } })).toEqual(FALLBACK);
  });
});
