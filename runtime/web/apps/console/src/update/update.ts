import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

const KEY = ["runtime-update"] as const;
const UPDATING_POLL_MS = 3_000;
const IDLE_POLL_MS = 600_000;

export type RuntimeUpdate = {
  installed: string | null;
  available: string | null;
  state: "idle" | "running" | "failed";
  error: string | null;
};

export type UpdateView =
  | { kind: "hidden" }
  | { kind: "offer"; available: string; installed: string | null; problem: string | null }
  | { kind: "updating" }
  | { kind: "done" };

// baseline is the first installed release this tab ever saw. install.sh
// removes runtime.version early, so installed is null for most of an update.
export function updateView(status: RuntimeUpdate | undefined, baseline: string | null): UpdateView {
  if (!status) return { kind: "hidden" };
  if (status.state === "running") return { kind: "updating" };
  if (status.installed !== null && baseline !== null && status.installed !== baseline) {
    return { kind: "done" };
  }
  if (status.available === null) return { kind: "hidden" };
  return {
    kind: "offer",
    available: status.available,
    installed: status.installed,
    problem: status.state === "failed" ? (status.error ?? "no reason given") : null,
  };
}

export function nextBaseline(baseline: string | null, installed: string | null): string | null {
  return baseline ?? installed;
}

// The API and web containers are recreated mid-update, so fetches fail for a
// while; the query keeps its last data and the poll carries on.
export function useRuntimeUpdate(baseline: string | null, starting: boolean) {
  return useQuery({
    queryKey: KEY,
    queryFn: () => api.get<RuntimeUpdate>("/api/runtime/update"),
    retry: false,
    refetchInterval: (query) =>
      starting || updateView(query.state.data, baseline).kind === "updating" ? UPDATING_POLL_MS : IDLE_POLL_MS,
  });
}

export function useStartUpdate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<RuntimeUpdate>("/api/runtime/update"),
    onSuccess: (status) => client.setQueryData(KEY, status),
  });
}
