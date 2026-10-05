import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { loadCatalog, platformFor, type Connect } from "./catalog";
import type { AgentStatuses } from "./status";

async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

export function useAgents() {
  const connect = useQuery({ queryKey: ["connect"], queryFn: () => api.get<Connect>("/api/connect"), staleTime: Infinity, retry: false });
  const catalog = useQuery({ queryKey: ["agents"], queryFn: () => loadCatalog(fetchJson), staleTime: Infinity });
  // Waits for /connect to settle either way: an API without it still gets
  // guides, chosen from the browser.
  const platform = platformFor(connect.data, navigator.userAgent);
  const agents = connect.isPending ? undefined : catalog.data?.filter((agent) => agent.platforms[platform]);
  return { platform, agents, connect: connect.data, error: catalog.error };
}

const POLL_MS = 3000;

// Polls only while a guide is open for an agent that hasn't connected; an API
// without these routes errors once and the console just shows no status.
export function useAgentStatus(watch?: string) {
  return useQuery({
    queryKey: ["agent-status"],
    queryFn: () => api.get<AgentStatuses>("/api/agents/status"),
    retry: false,
    refetchInterval: (query) =>
      watch && !query.state.error && !query.state.data?.agents[watch]?.connected ? POLL_MS : false,
  });
}

export function useEnsureSetup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<{ requested: boolean }>(`/api/agents/${id}/setup`),
    onSettled: () => client.invalidateQueries({ queryKey: ["agent-status"] }),
  });
}
