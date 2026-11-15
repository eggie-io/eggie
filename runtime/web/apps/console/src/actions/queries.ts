import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ActionRun, ActionsAnswer, AnswerBody, ConnectionAnswer, ConnectionRun, Run } from "./types";

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
  return api.text(`${base(projectId)}/${encodeURIComponent(actionId)}/log`);
}

export function actionOutput(projectId: string, actionId: string): Promise<string> {
  return api.text(`${base(projectId)}/${encodeURIComponent(actionId)}/output`);
}

const connectionBase = (id: string) => `/api/connections/${encodeURIComponent(id)}`;
const connectionKey = (id: string) => ["connection", id] as const;

export function useConnection(id: string, enabled = true) {
  return useQuery({
    queryKey: connectionKey(id),
    queryFn: () => api.get<ConnectionAnswer>(connectionBase(id)),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.state === "checking" || query.state.data?.run?.state === "running" ? 1_000 : 30_000,
  });
}

function useConnectionVerb(id: string, verb: "login" | "cancel") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ run: ConnectionRun | null }>(`${connectionBase(id)}/${verb}`),
    onSettled: () => client.invalidateQueries({ queryKey: connectionKey(id) }),
  });
}

export const useConnectionLogin = (id: string) => useConnectionVerb(id, "login");
export const useConnectionCancel = (id: string) => useConnectionVerb(id, "cancel");

// The value lives only in the mutation call; it is never a key, meta or cached data.
export function useAnswerConnection(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["answer-connection", id],
    mutationFn: (body: AnswerBody) => api.post<Record<string, never>>(`${connectionBase(id)}/answer`, body),
    onSettled: () => client.invalidateQueries({ queryKey: connectionKey(id) }),
  });
}

export function useActionRun(projectId: string, actionId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...key(projectId), actionId, "run"] as const,
    queryFn: () => api.get<ActionRun>(`${base(projectId)}/${encodeURIComponent(actionId)}/run`),
    enabled,
    refetchInterval: 1_000,
  });
}

export function useAnswerAction(projectId: string, actionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["answer-action", projectId, actionId],
    mutationFn: (body: AnswerBody) =>
      api.post<Record<string, never>>(`${base(projectId)}/${encodeURIComponent(actionId)}/answer`, body),
    onSettled: () => client.invalidateQueries({ queryKey: [...key(projectId), actionId, "run"] }),
  });
}
