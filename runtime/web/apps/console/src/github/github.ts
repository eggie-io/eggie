import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export const DEVICE_URL = "https://github.com/login/device";
export const REVOKE_URL = "https://github.com/settings/applications";
const POLL_MS = 2000;
const GITHUB = ["github"] as const;

export type SetupState = "applying" | "ready" | "failed" | "runtime_outdated";

export type GitHubStatus =
  | { state: "disconnected"; error: string | null }
  | { state: "pending"; user_code: string; url: string; expires_at: number }
  | { state: "connected"; login: string; name: string; email: string; setup: SetupState; setup_error: string | null }
  | { state: "needs_reconnect"; login: string };

export type Repo = {
  full_name: string;
  private: boolean;
  description: string | null;
  updated_at: number | null;
  project: string | null;
  name_taken: boolean;
  suggested_id: string | null;
};
export type RepoPage = { repos: Repo[]; has_more: boolean };

export function polling(status?: GitHubStatus): boolean {
  return status?.state === "pending" || (status?.state === "connected" && status.setup === "applying");
}

// A server-side move to needs_reconnect (e.g. a clone that failed on a
// revoked token) would otherwise stay hidden behind the last-fetched status.
export function invalidateGitHub(client: QueryClient) {
  return client.invalidateQueries({ queryKey: GITHUB });
}

// The list marks repos that already are projects, so a landed clone changes it.
export function invalidateRepos(client: QueryClient) {
  return client.invalidateQueries({ queryKey: [...GITHUB, "repos"] });
}

export function useGitHub() {
  return useQuery({
    queryKey: GITHUB,
    queryFn: () => api.get<GitHubStatus>("/api/github"),
    refetchInterval: (query) => (polling(query.state.data) ? POLL_MS : false),
  });
}

function useStatusMutation(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<GitHubStatus>(path),
    onSuccess: (status) => client.setQueryData(GITHUB, status),
  });
}

export const useConnectGitHub = () => useStatusMutation("/api/github/connect");
export const useReapplyGitHub = () => useStatusMutation("/api/github/reapply");

export function useDisconnectGitHub() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<GitHubStatus>("/api/github/disconnect"),
    onSuccess: (status) => {
      client.setQueryData(GITHUB, status);
      // Repos belonged to the account that just disconnected.
      client.removeQueries({ queryKey: [...GITHUB, "repos"] });
    },
  });
}

export function useRepos(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: [...GITHUB, "repos"],
    enabled,
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api.get<RepoPage>(`/api/github/repos?page=${pageParam}`),
    getNextPageParam: (last, all) => (last.has_more ? all.length + 1 : undefined),
  });
}

export function useCloneRepo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ repo, id }: { repo: string; id?: string }) =>
      api.post<{ job_id: string; id: string }>("/api/github/clone", id ? { repo, id } : { repo }),
    // A revoked token turns the status to needs_reconnect server-side.
    onError: () => void client.invalidateQueries({ queryKey: GITHUB }),
  });
}
