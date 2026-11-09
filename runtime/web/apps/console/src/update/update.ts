import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";

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

export function updateView(status: RuntimeUpdate | undefined, startedFrom: string | null): UpdateView {
  if (!status) return { kind: "hidden" };
  if (startedFrom !== null && status.state !== "running" && status.installed !== startedFrom) {
    return { kind: "done" };
  }
  if (status.state === "running") return { kind: "updating" };
  if (status.available === null) return { kind: "hidden" };
  return {
    kind: "offer",
    available: status.available,
    installed: status.installed,
    problem: status.state === "failed" ? (status.error ?? "no reason given") : null,
  };
}

// A tab that only watched someone else's update still needs to know where
// it started, so it reloads once the release moves.
export function latchStart(view: UpdateView, startedFrom: string | null, installed: string | null): string | null {
  return view.kind === "updating" && startedFrom === null ? installed : startedFrom;
}

// The request failed for good, as opposed to an answer lost while the API restarts.
export function refusedStart(error: unknown): boolean {
  return error instanceof ApiError && (error.code === "update_not_available" || error.code === "update_unavailable");
}

// The API and web containers are recreated mid-update, so fetches fail for a
// while; the query keeps its last data and the poll carries on.
export function useRuntimeUpdate(startedFrom: string | null, starting: boolean) {
  return useQuery({
    queryKey: KEY,
    queryFn: () => api.get<RuntimeUpdate>("/api/runtime/update"),
    retry: false,
    refetchInterval: (query) =>
      starting || updateView(query.state.data, startedFrom).kind === "updating" ? UPDATING_POLL_MS : IDLE_POLL_MS,
  });
}

export function useStartUpdate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<RuntimeUpdate>("/api/runtime/update"),
    onSuccess: (status) => client.setQueryData(KEY, status),
  });
}
