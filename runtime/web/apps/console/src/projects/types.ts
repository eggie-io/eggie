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

export type JobKind = "up" | "restart" | "down" | "clone" | "push" | "import";

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

export interface PushSummary {
  blocked: "site" | "signed_out" | null;
  last: { instance_id: string; console_hostname: string; pushed_at: number } | null;
}

export interface Project {
  id: string;
  status: Status;
  domain: string;
  path: string;
  urls: string[];
  problem: Problem | null;
  empty: boolean;
  origin: "new" | "github" | "folder";
  ran_ok: boolean;
  repo: string | null;
  web: WebEntry[];
  first_run: boolean;
  job: ActiveJob | null;
  public: PublicStatus;
  restart_needed: boolean;
  secrets_requested: number;
  push: PushSummary;
  start_when_ready: boolean;
}

export interface SecretsView {
  secrets: { name: string; updated_at: number; unused: boolean }[];
  requested: { name: string; hint: string }[];
  restart_needed: boolean;
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
  progress?: { done: number; total: number } | null;
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
