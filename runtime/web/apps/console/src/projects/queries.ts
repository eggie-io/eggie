import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DeletePreview, DeleteResult, Job, Project, ProjectList, PublicStatus } from "./types";

const BUSY_MS = 3000;
const IDLE_MS = 15_000;
const JOB_MS = 1000;

const ALL = ["projects"] as const;
const LIST = ["projects", "list"] as const;
export const ONE = (id: string) => ["projects", "one", id] as const;

export function projectPath(id: string): string {
  return `/api/projects/${encodeURIComponent(id)}`;
}

const settling = (project: Project) => project.job !== null || project.public.state === "enabling";

export function useProjects() {
  return useQuery({
    queryKey: LIST,
    queryFn: () => api.get<ProjectList>("/api/projects"),
    refetchInterval: (query) => (query.state.data?.projects.some(settling) ? BUSY_MS : IDLE_MS),
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ONE(id),
    queryFn: () => api.get<Project>(projectPath(id)),
    refetchInterval: (query) => (query.state.data && settling(query.state.data) ? BUSY_MS : IDLE_MS),
  });
}

export function useJob(jobId: string) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["jobs", jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),
    refetchInterval: (q) => (q.state.error || (q.state.data && q.state.data.state !== "running") ? false : JOB_MS),
  });
  // A finished or forgotten job (the API restarted) means the project has
  // moved on; refetch it now rather than at the next poll.
  const settled = query.isError || (query.data !== undefined && query.data.state !== "running");
  useEffect(() => {
    if (settled) void client.invalidateQueries({ queryKey: ALL });
  }, [settled, client]);
  return query;
}

export function useLifecycle(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: "up" | "down" | "restart") => api.post<{ job_id: string }>(`${projectPath(id)}/${action}`),
    onSettled: () => client.invalidateQueries({ queryKey: ALL }),
  });
}

export function usePublic(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (on: boolean) =>
      on ? api.post<PublicStatus>(`${projectPath(id)}/public`) : api.del<PublicStatus>(`${projectPath(id)}/public`),
    onSettled: () => client.invalidateQueries({ queryKey: ALL }),
  });
}

export function useCreateProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<Project>("/api/projects", { id: name }),
    onSuccess: () => client.invalidateQueries({ queryKey: LIST }),
  });
}

export function useAdopt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<Project>(`${projectPath(name)}/adopt`),
    onSettled: () => client.invalidateQueries({ queryKey: LIST }),
  });
}

export function useDeletePreview(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["delete-preview", id],
    queryFn: () => api.get<DeletePreview>(`${projectPath(id)}/delete-preview`),
    enabled,
    staleTime: 0,
    gcTime: 0,
  });
}

export function useDeleteProject(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (purge: boolean) => api.del<DeleteResult>(`${projectPath(id)}${purge ? "?purge=true" : ""}`),
    onSuccess: () => {
      // Update the list eagerly so the deleted row is gone before navigate("/")
      // paints; the single-project cache is removed by the list once it has
      // taken over, so a still-mounted project page never re-fetches into a 404.
      client.setQueryData<ProjectList>(LIST, (d) => d && { ...d, projects: d.projects.filter((p) => p.id !== id) });
      void client.invalidateQueries({ queryKey: LIST });
    },
  });
}

export function useLogs(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["logs", id],
    queryFn: () => api.text(`${projectPath(id)}/logs`),
    enabled,
  });
}
