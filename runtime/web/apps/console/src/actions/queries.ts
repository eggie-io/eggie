import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ActionsAnswer, Run } from "./types";

const base = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/actions`;
const key = (projectId: string) => ["actions", projectId] as const;

export function useProjectActions(projectId: string) {
  return useQuery({
    queryKey: key(projectId),
    queryFn: () => api.get<ActionsAnswer>(base(projectId)),
    refetchInterval: (query) => (query.state.data?.actions.some((a) => a.run?.state === "running") ? 3_000 : 15_000),
  });
}

function useRunMutation(projectId: string, verb: "start" | "stop") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (actionId: string) =>
      api.post<{ run: Run | null }>(`${base(projectId)}/${encodeURIComponent(actionId)}/${verb}`),
    // A 409 means it was already running; the list still has to catch up.
    onSettled: () => client.invalidateQueries({ queryKey: key(projectId) }),
  });
}

export const useStartAction = (projectId: string) => useRunMutation(projectId, "start");
export const useStopAction = (projectId: string) => useRunMutation(projectId, "stop");

export function actionLog(projectId: string, actionId: string): Promise<string> {
  return api.text(`${base(projectId)}/${encodeURIComponent(actionId)}/log?tail=200`);
}
