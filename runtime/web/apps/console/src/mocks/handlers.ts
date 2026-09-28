import { delay, http, HttpResponse } from "msw";
import { slugify } from "../projects/slugify";
import type { Discovered, Job, JobKind, Project } from "../projects/types";
import { baseName, joinPath, parentOf } from "../uploads/paths";

export const SCENARIOS = [
  "ok",
  "empty",
  "expired",
  "handoff-spent",
  "old-api",
  "down",
  "lost-mid-use",
  "wrong-host",
  "uploads",
  "full",
  "fills-up",
  "busy",
  "locked",
  "account-needed",
  "account-pending",
  "account-denied",
  "account-unreachable",
  "public-on",
  "public-expiring",
  "public-active-elsewhere",
  "public-unavailable",
  "github-pending",
  "github-denied",
  "github-expired",
  "github-applying",
  "github-outdated",
  "github-reconnect",
  "github-clone-fails",
  "windows",
] as const;
export type Scenario = (typeof SCENARIOS)[number];

export function scenarioFrom(search: string): Scenario {
  const wanted = new URLSearchParams(search).get("scenario");
  return SCENARIOS.find((scenario) => scenario === wanted) ?? "ok";
}

const DOMAIN = "127-0-0-1.sslip.io";
const address = (id: string, sub?: string) => `http://${sub ? `${sub}.` : ""}${id}.${DOMAIN}:39080`;
const nowSec = () => Date.now() / 1000;

const LOOPBACK = { code: "bound_to_loopback", message: "the web service listens on 127.0.0.1 inside its container" };
const NO_COMPOSE = { code: "compose_missing", message: "there's no docker-compose.yml in this project yet" };

function project(id: string, over: Partial<Project> = {}): Project {
  return {
    id,
    status: "stopped",
    domain: DOMAIN,
    path: `/opt/omelet/projects/${id}`,
    urls: [address(id)],
    problem: null,
    empty: false,
    web: [{ url: address(id), service: "web", primary: true }],
    first_run: false,
    job: null,
    public: { state: "off", note: null },
    ...over,
  };
}

function emptyProject(id: string): Project {
  return project(id, { urls: [], web: [], empty: true, problem: NO_COMPOSE, first_run: true });
}

const LOGS = `11:04:19 photo-sorter_web  listening on 127.0.0.1:8000
11:04:22 proxy  GET / → ECONNREFUSED 172.19.0.4:8000
11:04:27 proxy  health check failed 3/3
`;

// Same codes and wording as the API's own refusals.
const refuse = (code: string, message: string, status: number) =>
  HttpResponse.json({ error: { code, message } }, { status });
const notSignedIn = () => refuse("not_signed_in", "open Omelet from the desktop app to sign in", 401);
const expired = () => refuse("session_expired", "your sign-in ran out; open Omelet from the desktop app again", 401);
const notFound = (id: string) => refuse("project_not_found", `no project called ${id}`, 404);
const busy = () => refuse("project_busy", "this project is busy with another job", 409);

export function handlersFor(scenario: Scenario) {
  let signedIn = !["expired", "handoff-spent", "down", "wrong-host"].includes(scenario);
  const MOCK_CODE = "WDJB-MJHT";
  const pendingAccount = () => ({
    state: "pending" as const,
    user_code: MOCK_CODE,
    url: `https://omelet.example/device?user_code=${MOCK_CODE}`,
    expires_at: nowSec() + 600,
  });
  let account: Record<string, unknown> = scenario.startsWith("account-")
    ? scenario === "account-pending"
      ? pendingAccount()
      : { state: "signed_out", error: null }
    : { state: "signed_in", email: "ada@example.com", sync: { last_ok_at: nowSec(), last_error: null } };
  let approveAt = 0;
  const GH_CODE = "C0DE-F00D";
  const ghConnected = (setup: string, setup_error: string | null = null) =>
    ({ state: "connected", login: "ada", name: "Ada", email: "1+ada@users.noreply.github.com", setup, setup_error });
  const ghPending = () => ({ state: "pending", user_code: GH_CODE, url: "https://github.com/login/device", expires_at: nowSec() + 900 });
  let github: Record<string, unknown> =
    scenario === "github-pending" ? ghPending()
    : scenario === "github-applying" ? ghConnected("applying")
    : scenario === "github-outdated" ? ghConnected("runtime_outdated")
    : scenario === "github-reconnect" ? { state: "needs_reconnect", login: "ada" }
    : { state: "disconnected", error: null };
  let ghSettleAt = 0;
  const projects = new Map<string, Project>();
  const discovered: Discovered[] = [];
  const jobs = new Map<string, Job>();

  function startJob(target: Project, kind: JobKind): Job {
    const id = Math.random().toString(16).slice(2, 14);
    const steps =
      kind === "down" ? ["preparing", "stopping"]
      : kind === "clone" ? ["cloning", "starting", "checking"]
      : ["preparing", "starting", "checking"];
    const stepMs = target.first_run && kind !== "down" ? 6000 : 2000;
    const job: Job = {
      job_id: id,
      kind,
      phase: steps[0],
      state: "running",
      detail: "",
      result: null,
      started_at: nowSec(),
      finished_at: null,
    };
    jobs.set(id, job);
    target.job = { id, kind, phase: steps[0], started_at: job.started_at };
    let step = 0;
    const tick = () => {
      // github-clone-fails exercises the failure path while still on the
      // "cloning" phase, before any other step runs.
      if (kind === "clone" && scenario === "github-clone-fails" && step === 0) {
        target.job = null;
        job.state = "failed";
        job.detail = "fatal: repository not found";
        job.finished_at = nowSec();
        return;
      }
      step += 1;
      if (step < steps.length) {
        job.phase = steps[step];
        if (target.job) target.job = { ...target.job, phase: job.phase };
        window.setTimeout(tick, stepMs);
        return;
      }
      target.job = null;
      job.finished_at = nowSec();
      if (kind === "down") {
        target.status = "stopped";
        job.state = "done";
        job.result = { status: "stopped" };
        return;
      }
      target.first_run = false;
      target.status = "started_ok";
      // photo-sorter never gets better, so "Try again" can be walked.
      target.problem = target.id === "photo-sorter" ? LOOPBACK : null;
      // started_ok with a diagnosed problem still finishes the job as done,
      // same as the real API; the problem lives on the project, in result.
      job.state = "done";
      job.detail = "";
      job.result = { status: target.status, urls: target.urls, problem: target.problem };
    };
    window.setTimeout(tick, stepMs);
    return job;
  }

  // Shared by POST /api/projects and the GitHub clone mock, so a bad name or
  // a clash with an existing project is refused the same way from both.
  function makeProject(rawId: string): Project | Response {
    const id = slugify(rawId);
    if (!id) return refuse("invalid_project", "a project needs a name made of letters, numbers or dashes", 422);
    if (projects.has(id)) return refuse("project_exists", `a project called ${id} already exists`, 409);
    const created = emptyProject(id);
    projects.set(id, created);
    return created;
  }

  if (scenario !== "empty") {
    const minutesAgo = (n: number) => nowSec() - n * 60;
    for (const p of [
      project("recipe-box", {
        status: "started_ok",
        urls: [address("recipe-box"), address("recipe-box", "api"), address("recipe-box", "studio")],
        web: [
          { url: address("recipe-box"), service: "web", primary: true },
          { url: address("recipe-box", "api"), service: "api", primary: false },
          { url: address("recipe-box", "studio"), service: "studio", primary: false },
        ],
      }),
      project("weekend-shop"),
      project("tiny-crm", { first_run: true }),
      project("photo-sorter", { status: "started_ok", problem: LOOPBACK }),
      emptyProject("notes-app"),
      project("old-blog", { urls: [], web: [], problem: { code: "folder_missing", message: "this project's folder is gone" } }),
    ]) {
      projects.set(p.id, p);
    }
    startJob(projects.get("tiny-crm")!, "up");
    discovered.push(
      { name: "invoice-helper", seen_at: minutesAgo(4), adoptable: true, reason: null },
      { name: "spice-rack", seen_at: minutesAgo(20), adoptable: false, reason: "compose_missing" },
      { name: "Tax Stuff", seen_at: minutesAgo(130), adoptable: false, reason: "bad_name" },
    );
    if (scenario === "public-on" || scenario === "public-expiring") {
      const box = projects.get("recipe-box")!;
      box.public = {
        state: "on",
        urls: [{ url: "https://k3x9m2p7qa.omelet.app", service: "web", local_url: address("recipe-box") }],
        expires_at: nowSec() + (scenario === "public-expiring" ? 20 : 42 * 60),
      };
    }
  }

  const GB = 1024 ** 3;
  interface Node { kind: "file" | "folder"; size: number; modified: number }
  const trees = new Map<string, Map<string, Node>>();
  const hoursAgo = (n: number) => nowSec() - n * 3600;

  function tree(id: string): Map<string, Node> {
    let t = trees.get(id);
    if (!t) {
      t = new Map();
      trees.set(id, t);
    }
    return t;
  }

  function put(id: string, path: string, node: Node): void {
    const t = tree(id);
    for (let dir = parentOf(path); dir !== ""; dir = parentOf(dir)) {
      if (!t.has(dir)) t.set(dir, { kind: "folder", size: 0, modified: node.modified });
    }
    t.set(path, node);
  }

  const seedFile = (id: string, path: string, size: number, modified: number) => put(id, path, { kind: "file", size, modified });
  if (projects.has("recipe-box")) {
    seedFile("recipe-box", "recipes-seed.csv", 2.1 * 1024 ** 2, hoursAgo(26));
    seedFile("recipe-box", "README.md", 4 * 1024, hoursAgo(72));
    seedFile("recipe-box", "package.json", 2 * 1024, nowSec() - 11 * 60);
    for (const name of ["orders.csv", "users.csv", "notes.txt"]) seedFile("recipe-box", `data/${name}`, 40 * 1024, hoursAgo(26));
    for (let i = 1; i <= 48; i += 1) seedFile("recipe-box", `public/img-${i}.png`, 90 * 1024, hoursAgo(2));
    for (let i = 1; i <= 112; i += 1) seedFile("recipe-box", `src/module-${i}.ts`, 3 * 1024, nowSec() - 11 * 60);
  }
  for (const p of projects.values()) {
    if (p.id === "recipe-box" || p.empty) continue;
    seedFile(p.id, "docker-compose.yml", 1024, hoursAgo(30));
    seedFile(p.id, "README.md", 2048, hoursAgo(30));
  }

  const disk = { free: scenario === "full" ? 2.1 * GB : 40 * GB, total: 64 * GB };

  interface MockUpload {
    id: string; project_id: string; path: string; size: number; offset: number;
    fingerprint: string; replace: boolean; updated_at: number; chunks: number; busyLeft: number;
  }
  const uploads = new Map<string, MockUpload>();
  const newUpload = (over: Omit<MockUpload, "id" | "updated_at" | "chunks" | "busyLeft">): MockUpload => {
    const id = Math.random().toString(16).slice(2).padEnd(32, "0").slice(0, 32);
    const up = { id, updated_at: nowSec(), chunks: 0, busyLeft: scenario === "busy" ? 2 : 0, ...over };
    uploads.set(id, up);
    return up;
  };
  if (scenario === "uploads" && projects.has("recipe-box")) {
    const media = Math.round(4.4 * GB);
    const photos = Math.round(1.2 * GB);
    newUpload({ project_id: "recipe-box", path: "data/media-library.zip", size: media, offset: Math.round(media * 0.38), fingerprint: `media-library.zip:${media}:0`, replace: false });
    newUpload({ project_id: "recipe-box", path: "data/studio-photos.zip", size: photos, offset: Math.round(photos * 0.71), fingerprint: `studio-photos.zip:${photos}:0`, replace: false });
  }
  const shown = ({ chunks: _c, busyLeft: _b, ...rest }: MockUpload) => rest;
  const noUpload = () => refuse("upload_not_found", "no such upload", 404);
  const traversal = (path: string) => path.startsWith("/") || path.split("/").includes("..");

  const guard = () => (signedIn ? null : notSignedIn());
  const find = (id: string) => projects.get(id) ?? null;

  const lifecycle = (action: "up" | "down" | "restart") =>
    http.post(`/api/projects/:id/${action}`, ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      if (target.job) return busy();
      if (action !== "down" && target.empty) return refuse(NO_COMPOSE.code, NO_COMPOSE.message, 400);
      return HttpResponse.json({ job_id: startJob(target, action).job_id }, { status: 202 });
    });

  return [
    http.get("/api/health", () => {
      if (scenario === "down") return HttpResponse.error();
      if (scenario === "wrong-host") return refuse("forbidden_host", "this address isn't allowed", 403);
      return HttpResponse.json({
        status: "ok",
        version: "0.2.0",
        api: scenario === "old-api" ? 2 : 1,
        docker: { reachable: true, version: "29.0.0", detail: "" },
      });
    }),
    http.post("/api/session", () => {
      if (scenario === "handoff-spent") {
        return refuse("handoff_invalid", "that sign-in link has already been used or has run out; open Omelet from the desktop app again", 401);
      }
      signedIn = true;
      return HttpResponse.json({ signed_in: true });
    }),
    http.get("/api/session", () => {
      if (signedIn) return HttpResponse.json({ signed_in: true });
      return scenario === "expired" ? expired() : notSignedIn();
    }),
    http.delete("/api/session", () => {
      signedIn = false;
      return HttpResponse.json({ signed_in: false });
    }),
    http.get("/api/account", () => {
      if (account.state === "pending" && approveAt === 0) approveAt = Date.now() + 6000;
      if (account.state === "pending" && Date.now() >= approveAt) {
        account =
          scenario === "account-denied"
            ? { state: "signed_out", error: "access_denied" }
            : { state: "signed_in", email: "ada@example.com", sync: { last_ok_at: nowSec(), last_error: null } };
      }
      return HttpResponse.json(account);
    }),
    http.post("/api/account/sign-in", () => {
      if (scenario === "account-unreachable") {
        return refuse("cloud_unavailable", "The Omelet service can't be reached. Check the internet connection and try again.", 503);
      }
      account = pendingAccount();
      approveAt = 0;
      return HttpResponse.json(account);
    }),
    http.post("/api/account/sign-out", () => {
      account = { state: "signed_out", error: null };
      return HttpResponse.json(account);
    }),
    http.post("/api/sessions/handoff", () => HttpResponse.json({ code: "mock-code", expires_in: 60 })),

    http.get("/api/github", () => {
      if ((github.state === "pending" || github.setup === "applying") && ghSettleAt === 0) ghSettleAt = Date.now() + 5000;
      if (ghSettleAt && Date.now() >= ghSettleAt) {
        ghSettleAt = 0;
        github =
          github.state === "connected" ? ghConnected("ready")
          : scenario === "github-denied" ? { state: "disconnected", error: "access_denied" }
          : scenario === "github-expired" ? { state: "disconnected", error: "expired_token" }
          : ghConnected("applying");
      }
      return HttpResponse.json(github);
    }),
    http.post("/api/github/connect", () => {
      github = ghPending();
      return HttpResponse.json(github);
    }),
    http.post("/api/github/disconnect", () => {
      github = { state: "disconnected", error: null };
      return HttpResponse.json(github);
    }),
    http.post("/api/github/reapply", () => {
      github = ghConnected("applying");
      return HttpResponse.json(github);
    }),
    http.get("/api/github/repos", ({ request }) => {
      const page = Number(new URL(request.url).searchParams.get("page") ?? "1");
      const names = page === 1 ? ["ada/recipe-site", "ada/garden-log", "kitchen-co/menu"] : ["ada/old-notes"];
      return HttpResponse.json({
        has_more: page === 1,
        repos: names.map((full_name, i) => ({ full_name, private: i % 2 === 0, description: null, updated_at: nowSec() - (i + page) * 86400 })),
      });
    }),
    http.post("/api/github/clone", async ({ request }) => {
      const denied = guard();
      if (denied) return denied;
      const { repo } = (await request.json()) as { repo: string };
      const created = makeProject(repo.split("/")[1] ?? repo);
      if (created instanceof Response) return created;
      const job = startJob(created, "clone");
      return HttpResponse.json({ job_id: job.job_id, id: created.id }, { status: 202 });
    }),

    http.get("/api/projects", async () => {
      if (scenario === "lost-mid-use") return expired();
      const denied = guard();
      if (denied) return denied;
      await delay(200);
      return HttpResponse.json({ projects: [...projects.values()], discovered });
    }),
    http.post("/api/projects", async ({ request }) => {
      const denied = guard();
      if (denied) return denied;
      const { id: raw } = (await request.json()) as { id: string };
      const created = makeProject(raw);
      return created instanceof Response ? created : HttpResponse.json(created, { status: 201 });
    }),
    http.get("/api/projects/:id", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      return target ? HttpResponse.json(target) : notFound(String(params.id));
    }),
    http.post("/api/projects/:id/adopt", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const name = String(params.id);
      const index = discovered.findIndex((folder) => folder.name === name);
      if (projects.has(name)) return refuse("project_exists", `a project called ${name} already exists`, 409);
      if (index === -1) return refuse("folder_not_found", `there's no folder called ${name}`, 404);
      if (!discovered[index].adoptable) return refuse("not_adoptable", `${name} can't be adopted yet`, 409);
      discovered.splice(index, 1);
      const adopted = project(name, { first_run: true });
      projects.set(name, adopted);
      return HttpResponse.json(adopted, { status: 201 });
    }),
    lifecycle("up"),
    lifecycle("down"),
    lifecycle("restart"),
    http.get("/api/jobs/:id", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const job = jobs.get(String(params.id));
      return job ? HttpResponse.json(job) : refuse("job_not_found", "no such job", 404);
    }),
    http.get("/api/projects/:id/logs", async ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      await delay(300);
      if (target.empty) return refuse("logs_unavailable", "there are no logs for this project yet", 409);
      const text = target.id === "photo-sorter" ? LOGS : `${target.id}_web  ready\n`;
      return new HttpResponse(text, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }),
    http.get("/api/projects/:id/delete-preview", async ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      await delay(400);
      if (target.empty) return HttpResponse.json({ files: 0, bytes: 0, containers: [], volumes: [] });
      return HttpResponse.json({
        files: 412,
        bytes: 38 * 1024 * 1024,
        containers: [`${target.id}-web-1`, `${target.id}-db-1`],
        volumes: [`${target.id}_db-data`],
      });
    }),
    http.delete("/api/projects/:id", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      if (target.job) return busy();
      projects.delete(target.id);
      trees.delete(target.id);
      for (const u of [...uploads.values()]) if (u.project_id === target.id) uploads.delete(u.id);
      // photo-sorter shows the "may still be running" outcome.
      const stopped = target.id !== "photo-sorter";
      return HttpResponse.json({ id: target.id, stopped, detail: stopped ? "" : "a container didn't stop in time" });
    }),
    http.post("/api/projects/:id/public", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      if (scenario === "public-unavailable")
        return refuse("not_registered", "This project isn't linked to your account yet. Try again in a minute.", 409);
      target.public = { state: "enabling" };
      window.setTimeout(() => {
        target.public =
          scenario === "public-active-elsewhere"
            ? { state: "failed", reason: { code: "public_url_active", message: "One is already on for another project or computer. Turn it off there first." } }
            : { state: "on", urls: target.web.map((w, i) => ({ url: i === 0 ? "https://k3x9m2p7qa.omelet.app" : `https://${w.service.toLowerCase().replace(/[_.]/g, "-")}--k3x9m2p7qa.omelet.app`, service: w.service, local_url: w.url })), expires_at: nowSec() + 60 * 60 };
      }, 2000);
      return HttpResponse.json(target.public, { status: 202 });
    }),
    http.delete("/api/projects/:id/public", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      target.public = { state: "off", note: null };
      return HttpResponse.json(target.public);
    }),
    http.get("/api/connect", () => {
      const denied = guard();
      if (denied) return denied;
      if (scenario === "windows") return HttpResponse.json({ vm: "wsl", ssh: null });
      return HttpResponse.json({ vm: "lima", ssh: { host: "127.0.0.1", port: 39022, user: "ada", key_file: "~/.lima/_config/user" } });
    }),
    http.get("/api/disk", () => {
      const denied = guard();
      if (denied) return denied;
      return HttpResponse.json({ free_bytes: disk.free, total_bytes: disk.total });
    }),
    http.get("/api/projects/:id/files", async ({ params, request }) => {
      const denied = guard();
      if (denied) return denied;
      const id = String(params.id);
      if (!find(id)) return notFound(id);
      const dir = joinPath(new URL(request.url).searchParams.get("dir") ?? "");
      await delay(250);
      if (scenario === "locked" && dir === "data") {
        return refuse("permission_denied", "Omelet can't look inside that folder; a program in the project owns it.", 409);
      }
      const t = tree(id);
      if (dir !== "" && t.get(dir)?.kind !== "folder") return refuse("folder_not_found", `no folder '${dir}' in project '${id}'`, 404);
      const entries = [...t.entries()]
        .filter(([path]) => parentOf(path) === dir)
        .map(([path, node]) => ({
          name: baseName(path),
          kind: node.kind,
          size: node.kind === "file" ? node.size : null,
          items: node.kind === "folder" ? [...t.keys()].filter((p) => parentOf(p) === path).length : null,
          modified: node.modified,
        }))
        .sort((a, b) => (a.kind === b.kind ? a.name.toLowerCase().localeCompare(b.name.toLowerCase()) : a.kind === "folder" ? -1 : 1));
      return HttpResponse.json({ dir, entries });
    }),
    http.get("/api/projects/:id/files/*", ({ params, request }) => {
      const denied = guard();
      if (denied) return denied;
      const id = String(params.id);
      if (!find(id)) return notFound(id);
      const path = decodeURIComponent(new URL(request.url).pathname.split("/files/")[1] ?? "");
      if (tree(id).get(path)?.kind !== "file") return refuse("file_not_found", `no file '${path}' in project '${id}'`, 404);
      return new HttpResponse(`mock contents of ${path}\n`, {
        headers: { "Content-Type": "application/octet-stream", "Content-Disposition": `attachment; filename="${baseName(path)}"` },
      });
    }),
    http.get("/api/projects/:id/uploads", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const id = String(params.id);
      if (!find(id)) return notFound(id);
      return HttpResponse.json({ uploads: [...uploads.values()].filter((u) => u.project_id === id).map(shown) });
    }),
    http.post("/api/projects/:id/uploads", async ({ params, request }) => {
      const denied = guard();
      if (denied) return denied;
      const id = String(params.id);
      if (!find(id)) return notFound(id);
      const body = (await request.json()) as { path: string; size: number; fingerprint?: string; replace?: boolean };
      if (traversal(body.path)) return refuse("path_traversal", `'${body.path}' escapes the project directory`, 400);
      const existing = tree(id).get(joinPath(body.path));
      if (existing?.kind === "folder") return refuse("path_is_folder", `'${body.path}' is a folder in the project`, 409);
      if (existing && !body.replace) return refuse("file_exists", `'${body.path}' is already in the project`, 409);
      if (body.size + GB > disk.free) {
        return HttpResponse.json({ error: { code: "not_enough_space", message: "this file is bigger than the room Omelet has left", free_bytes: disk.free } }, { status: 507 });
      }
      if (body.size === 0) {
        put(id, joinPath(body.path), { kind: "file", size: 0, modified: nowSec() });
        return HttpResponse.json({ upload_id: "0".repeat(32), offset: 0, size: 0, done: true }, { status: 201 });
      }
      const up = newUpload({ project_id: id, path: joinPath(body.path), size: body.size, offset: 0, fingerprint: body.fingerprint ?? "", replace: body.replace ?? false });
      return HttpResponse.json({ upload_id: up.id, offset: 0, size: up.size, chunk_size: 8 * 1024 * 1024, done: false }, { status: 201 });
    }),
    http.get("/api/uploads/:uploadId", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const up = uploads.get(String(params.uploadId));
      return up ? HttpResponse.json(shown(up)) : noUpload();
    }),
    http.patch("/api/uploads/:uploadId", async ({ params, request }) => {
      const denied = guard();
      if (denied) return denied;
      const up = uploads.get(String(params.uploadId));
      if (!up) return noUpload();
      const offset = Number(request.headers.get("Upload-Offset"));
      // Only the length is kept; the bytes are dropped on the floor.
      const length = (await request.arrayBuffer()).byteLength;
      await delay(350);
      if (offset !== up.offset) {
        return HttpResponse.json({ error: { code: "offset_mismatch", message: "the upload is at a different offset", offset: up.offset } }, { status: 409 });
      }
      up.chunks += 1;
      if (scenario === "fills-up" && up.size > 20 * 1024 ** 2 && up.chunks === 3 && disk.free > 0) {
        disk.free = 0;
        // One-shot: the next /disk read reports room again, as if the user freed some.
        window.setTimeout(() => { disk.free = 40 * GB; }, 0);
        return HttpResponse.json({ error: { code: "disk_full", message: "Omelet ran out of room", offset: up.offset } }, { status: 507 });
      }
      up.offset += length;
      up.updated_at = nowSec();
      if (up.offset < up.size) return HttpResponse.json({ upload_id: up.id, offset: up.offset, size: up.size, done: false });
      if (up.busyLeft > 0) {
        up.busyLeft -= 1;
        return busy();
      }
      if (!find(up.project_id)) {
        uploads.delete(up.id);
        return notFound(up.project_id);
      }
      put(up.project_id, up.path, { kind: "file", size: up.size, modified: nowSec() });
      uploads.delete(up.id);
      return HttpResponse.json({ upload_id: up.id, offset: up.size, size: up.size, done: true });
    }),
    http.delete("/api/uploads/:uploadId", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      if (!uploads.delete(String(params.uploadId))) return noUpload();
      return HttpResponse.json({ upload_id: String(params.uploadId), cancelled: true });
    }),
  ];
}
