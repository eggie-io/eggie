export type SetupState = "installing" | "ready" | "failed" | null;
export interface AgentState { connected: boolean; setup: SetupState }
export interface AgentStatuses { agents: Record<string, AgentState> }

export type LineKind = "connected" | "installing" | "failed" | "waiting";
export interface Line { kind: LineKind; text: string }

export function statusLine(name: string, state: AgentState | undefined): Line {
  if (state?.connected) return { kind: "connected", text: `${name} is connected` };
  if (state?.setup === "installing") return { kind: "installing", text: `Getting ${name} ready in your kitchen…` };
  if (state?.setup === "failed") return { kind: "failed", text: `Couldn't get ${name} ready.` };
  return { kind: "waiting", text: `Waiting for ${name} to connect…` };
}
