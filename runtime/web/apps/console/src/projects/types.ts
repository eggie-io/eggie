export type Status = "stopped" | "started_ok" | "failed_to_start" | "crash_looping";

export interface Problem {
  code: string;
  message: string;
}

export interface WebEntry {
  url: string;
  service: string;
  primary: boolean;
}

export type JobKind = "up" | "restart" | "down" | "clone";

export interface ActiveJob {
  id: string;
  kind: JobKind;
  phase: string;
  started_at: number;
}

export interface PublicUrl {
  url: string;
  service: string;
  local_url: string;
}

export type PublicStatus =
  | { state: "unavailable"; reason: Problem }
  | { state: "off"; note: Problem | null }
  | { state: "enabling" }
  | { state: "on"; urls: PublicUrl[]; expires_at: number | null }
  | { state: "failed"; reason: Problem };

export interface Project {
  id: string;
  status: Status;
  domain: string;
  path: string;
  urls: string[];
  problem: Problem | null;
  empty: boolean;
  web: WebEntry[];
  first_run: boolean;
  job: ActiveJob | null;
  public: PublicStatus;
}

export interface Discovered {
  name: string;
  seen_at: number;
  adoptable: boolean;
  reason: "bad_name" | "compose_missing" | null;
}

export interface ProjectList {
  projects: Project[];
  discovered: Discovered[];
}

export interface Job {
  job_id: string;
  kind: JobKind;
  phase: string;
  state: "running" | "done" | "failed";
  detail: string;
  result: unknown;
  started_at: number;
  finished_at: number | null;
}

export interface DeletePreview {
  files: number;
  bytes: number;
  containers: string[];
  volumes: string[];
}

export interface DeleteResult {
  id: string;
  stopped: boolean;
  detail: string;
}
