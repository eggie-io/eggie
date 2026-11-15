import { useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ActionRun, ActionsAnswer, AnswerBody, ConnectionAnswer, ConnectionRun, ConnectionState, Run } from "./types";

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

const connectionQuery = (id: string) => ({
  queryKey: connectionKey(id),
  queryFn: () => api.get<ConnectionAnswer>(connectionBase(id)),
  refetchInterval: (query: { state: { data?: ConnectionAnswer } }) =>
    query.state.data?.state === "checking" || query.state.data?.run?.state === "running" ? 1_000 : 30_000,
});

export function useConnection(id: string, enabled = true) {
  return useQuery({ ...connectionQuery(id), enabled });
}

// Same keys as useConnection, so a card's gate and its rows share one poll per connection.
export function useConnectionStates(ids: string[]): Record<string, ConnectionState | undefined> {
  return useQueries({
    queries: ids.map(connectionQuery),
    combine: (results) => Object.fromEntries(ids.map((id, i) => [id, results[i]?.data?.state])),
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

// Not a useMutation: a mutation keeps its variables (the typed value) on its observer and in the
// MutationCache. Here the value is held only by the request in flight.
function useAnswer(path: string, settledKey: readonly unknown[]) {
  const client = useQueryClient();
  const [sending, setSending] = useState(false);
  const send = (body: AnswerBody, onError: (error: unknown) => void) => {
    setSending(true);
    api
      .post<Record<string, never>>(path, body)
      .catch(onError)
      .finally(() => {
        setSending(false);
        void client.invalidateQueries({ queryKey: settledKey });
      });
  };
  return { send, sending };
}

export const useAnswerConnection = (id: string) => useAnswer(`${connectionBase(id)}/answer`, connectionKey(id));

export function useActionRun(projectId: string, actionId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...key(projectId), actionId, "run"] as const,
    queryFn: () => api.get<ActionRun>(`${base(projectId)}/${encodeURIComponent(actionId)}/run`),
    enabled,
    refetchInterval: 1_000,
  });
}

export const useAnswerAction = (projectId: string, actionId: string) =>
  useAnswer(`${base(projectId)}/${encodeURIComponent(actionId)}/answer`, [...key(projectId), actionId, "run"]);
