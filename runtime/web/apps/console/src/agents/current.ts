import type { Agent } from "./catalog";
import type { AgentStatuses } from "./status";

export interface CurrentAgent {
  name: string;
  connected: boolean;
  id: string | null;
}

const NOBODY: CurrentAgent = { name: "your coding agent", connected: false, id: null };

export function currentAgent(agents: Agent[] | undefined, statuses: AgentStatuses | undefined): CurrentAgent {
  const found = agents?.find((agent) => statuses?.agents[agent.id]?.connected);
  return found ? { name: found.name, connected: true, id: found.id } : NOBODY;
}

export const atSentenceStart = (name: string) => name.charAt(0).toUpperCase() + name.slice(1);
