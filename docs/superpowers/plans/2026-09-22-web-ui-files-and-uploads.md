# Web UI part D — files and uploads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in user browses a project's files, downloads one, and uploads files (big ones included) with a resumable, pausable queue that survives navigation, a busy project, a full disk and a reload.

**Architecture:** A React-free `UploadQueue` class in `apps/console/src/uploads/queue.ts` owns the agent's chunked-upload protocol and is tested against a scripted fake agent. One instance lives above the router (`QueueProvider`), components read it through `useSyncExternalStore`. Screens live in `apps/console/src/screens/files/`; the MSW mock agent grows a file tree, `/disk` and the upload routes.

**Tech Stack:** React 19, TypeScript 7, Vite 8, TanStack Query 5, React Router 8, MSW 2, Vitest 5 (node environment).

**Spec:** `docs/superpowers/specs/2026-09-22-web-ui-files-and-uploads-design.md`

## Global Constraints

- All paths below are relative to `web/` unless they start with `docs/` or `tests/`. Run npm commands from `web/`.
- No agent (`agent/`) changes.
- Kit is `@eggie/ui` (`Button`, `Modal`, `Notice`, `ProgressBar`, `PromptCard`, `RowCard`, `TextField`, `Collapsible`, `cx`). Do not import from `host/desktop/ui`.
- Vitest runs in `environment: "node"`; test files match `apps/*/src/**/*.test.ts` (`.ts`, not `.tsx`). No component or snapshot tests.
- Tests follow the user's testing rules: each test names the bug it catches; no tests of the framework, the mock, or trivial glue.
- Comments only for edge cases/workarounds, no ticket or doc references.
- The agent's error body is `{"error": {"code", "message", ...extra}}`; `offset` and `free_bytes` live inside `error`.
- `chunk_size` is 8 MiB (`8 * 1024 * 1024`); the space reserve is 1 GiB (`1024 ** 3`); a file fits when `size + 1 GiB <= free_bytes`.
- Upload client timeout: 120 000 ms. Busy retry every 3000 ms. Network retries: three status reads after 2000, 4000, 8000 ms.
- Copy must never say uploads continue with the page closed. Footer copy is exactly: "Pausing is fine — nothing is lost. If you close this page, uploads pick up where they stopped when you're back."
- Out-of-space copy points at the desktop app as text, never a link.
- Commits end with:
  ```
  Co-Authored-By: <your model> <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011hzoeJBHDyDeBtgqhm7gVY
  ```
- The working tree has the user's own uncommitted changes (`CLAUDE.md`, `README.md`, `web/Dockerfile`, `packaging/images/`, `web/.vitest/`). Never stage, commit, revert or edit them. Always `git add` explicit paths, never `git add -A`/`.`.
- Verification commands (from `web/`): `npm test`, `npm run typecheck`, `npm run build && npm run check-offline`.

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/console/src/api/client.ts` (modify) | `ApiError.details`, `Api.patch` with a raw body, external abort → `aborted` |
| `apps/console/src/uploads/paths.ts` | Path joining, folder-name validation, URLs for listing/download/routes |
| `apps/console/src/uploads/kinds.ts` | `kindOf(name)` and the coding-agent prompt per kind |
| `apps/console/src/uploads/eta.ts` | Speed samples → seconds left → words |
| `apps/console/src/uploads/uploadApi.ts` | Typed upload/disk calls on a 120 s client |
| `apps/console/src/uploads/queue.ts` | `UploadQueue`: the protocol, states, controls |
| `apps/console/src/uploads/QueueProvider.tsx` | One queue per signed-in app, context, `beforeunload`, last landed item |
| `apps/console/src/uploads/queries.ts` | `useListing`, `useDisk` |
| `apps/console/src/uploads/copy.ts` | Row captions, failure copy, summary line |
| `apps/console/src/screens/files/FilesPage.tsx` | Route `/p/:id/files/*`: header, breadcrumb, listing, drop, picker |
| `apps/console/src/screens/files/Listing.tsx` | Folder/file rows |
| `apps/console/src/screens/files/DestinationModal.tsx` | Frames 08 and 13 |
| `apps/console/src/screens/files/UploadPanel.tsx` | Frames 09 and 14 |
| `apps/console/src/screens/files/DoneCard.tsx` | Frame 10 |
| `apps/console/src/screens/files/FilesPage.module.css` | Styles for the above |
| `apps/console/src/App.tsx`, `screens/project/Tiles.tsx`, `screens/project/ProjectPage.tsx` (modify) | Route, provider, Files tile link |
| `apps/console/src/mocks/handlers.ts` (modify) | File tree, `/disk`, uploads, download, new scenarios |

---

### Task 1: Client — error details, raw PATCH, abort vs unreachable

**Files:**
- Modify: `apps/console/src/api/client.ts`
- Test: `apps/console/src/api/client.test.ts`

**Interfaces:**
- Produces:
  - `class ApiError { code: string; status: number; details: Record<string, unknown> }` — constructor `(code, message, status, details = {})`.
  - `Api.patch<T>(path: string, body: Blob, headers: Record<string, string>, signal?: AbortSignal): Promise<T>`
  - An abort triggered by the caller's `signal` rejects with `ApiError("aborted", "the request was cancelled", 0)`; a timeout or refused connection still rejects with `unreachable`.

- [ ] **Step 1: Write the failing tests** — append inside the `describe("the API client", …)` block of `client.test.ts`:

```ts
  it("keeps the agent's extra error fields so an upload can resume from the real offset", async () => {
    const { api } = answering(409, JSON.stringify({ error: { code: "offset_mismatch", message: "m", offset: 8388608 } }));
    const error = await failure(api.get("/api/uploads/x"));
    expect(error.details).toEqual({ offset: 8388608 });
  });

  it("sends a PATCH body raw, with its headers, not as JSON", async () => {
    const { api, seen } = answering(200, JSON.stringify({ offset: 3 }));
    const chunk = new Blob(["abc"]);
    await api.patch("/api/uploads/x", chunk, { "Upload-Offset": "0" });
    expect(seen[0]).toMatchObject({
      method: "PATCH",
      headers: { "Content-Type": "application/octet-stream", "Upload-Offset": "0" },
      body: chunk,
    });
  });

  it("reports a request the caller cancelled as 'aborted', not as a dropped connection", async () => {
    const fetchImpl = ((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal!.reason));
      })) as typeof fetch;
    const api = createApi(fetchImpl);
    const controller = new AbortController();
    const pending = failure(api.patch("/api/uploads/x", new Blob(["a"]), {}, controller.signal));
    controller.abort();
    const error = await pending;
    expect([error.code, error.status]).toEqual(["aborted", 0]);
  });
```

- [ ] **Step 2: Run to see them fail**

Run: `npx vitest run apps/console/src/api/client.test.ts`
Expected: the three new tests FAIL (`details` undefined; `api.patch is not a function`).

- [ ] **Step 3: Implement** — replace the body of `client.ts` from `export class ApiError` through the end of `createApi` with:

```ts
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type SessionLoss = "not_signed_in" | "session_expired";

export function isSessionLost(error: unknown): error is ApiError & { code: SessionLoss } {
  return (
    error instanceof ApiError &&
    error.status === 401 &&
    (error.code === "not_signed_in" || error.code === "session_expired")
  );
}

export interface Api {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body: Blob, headers: Record<string, string>, signal?: AbortSignal): Promise<T>;
  del<T>(path: string): Promise<T>;
  text(path: string): Promise<string>;
}

interface AgentError {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

function agentError(body: unknown): AgentError | null {
  if (typeof body !== "object" || body === null) return null;
  const error = (body as { error?: unknown }).error;
  if (typeof error !== "object" || error === null) return null;
  const { code, message, ...details } = error as Record<string, unknown>;
  return typeof code === "string" && typeof message === "string" ? { code, message, details } : null;
}

function parse(text: string): { ok: true; value: unknown } | { ok: false } {
  if (text === "") return { ok: true, value: undefined };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

interface Payload {
  json?: unknown;
  raw?: Blob;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export function createApi(fetchImpl: typeof fetch, { timeoutMs = 10_000 }: { timeoutMs?: number } = {}): Api {
  async function send(method: string, path: string, payload: Payload = {}): Promise<{ status: number; text: string }> {
    const timeout = AbortSignal.timeout(timeoutMs);
    const signal = payload.signal ? AbortSignal.any([payload.signal, timeout]) : timeout;
    const headers: Record<string, string> = {
      ...(payload.json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(payload.raw !== undefined ? { "Content-Type": "application/octet-stream" } : {}),
      ...payload.headers,
    };
    const body = payload.json !== undefined ? JSON.stringify(payload.json) : payload.raw;
    let status: number;
    let ok: boolean;
    let text: string;
    try {
      // Origin is left to the browser: the agent refuses a non-GET without it.
      // A hung agent must not hang the page: an aborted fetch rejects and
      // falls into the same "unreachable" mapping below as a refused one.
      const response = await fetchImpl(path, {
        method,
        credentials: "same-origin",
        signal,
        ...(Object.keys(headers).length > 0 ? { headers } : {}),
        ...(body !== undefined ? { body } : {}),
      });
      status = response.status;
      ok = response.ok;
      text = await response.text();
    } catch {
      // The caller's own abort (a paused upload) is not a dropped connection.
      if (payload.signal?.aborted) throw new ApiError("aborted", "the request was cancelled", 0);
      throw new ApiError("unreachable", "Eggie's service isn't answering", 0);
    }
    if (!ok) {
      const parsed = parse(text);
      const error = parsed.ok ? agentError(parsed.value) : null;
      if (error) throw new ApiError(error.code, error.message, status, error.details);
      throw new ApiError("unexpected", `unexpected answer (${status})`, status);
    }
    return { status, text };
  }

  async function request<T>(method: string, path: string, payload?: Payload): Promise<T> {
    const { status, text } = await send(method, path, payload);
    const parsed = parse(text);
    if (!parsed.ok) throw new ApiError("unexpected", "the answer wasn't JSON", status);
    return parsed.value as T;
  }

  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body === undefined ? {} : { json: body }),
    patch: (path, body, headers, signal) => request("PATCH", path, { raw: body, headers, signal }),
    del: (path) => request("DELETE", path),
    text: async (path) => (await send("GET", path)).text,
  };
}
```

Keep the existing `export const api = …` and `signOut` below it unchanged.

- [ ] **Step 4: Run the client tests and typecheck**

Run: `npx vitest run apps/console/src/api/client.test.ts && npm run typecheck`
Expected: all client tests PASS (including the old POST test, whose `headers`/`body` expectation is unchanged); typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add apps/console/src/api/client.ts apps/console/src/api/client.test.ts
git commit -m "Keep agent error details and add a raw, cancellable PATCH to the client"
```

---

### Task 2: Pure helpers — paths, kinds, time left

**Files:**
- Create: `apps/console/src/uploads/paths.ts`, `apps/console/src/uploads/kinds.ts`, `apps/console/src/uploads/eta.ts`
- Test: `apps/console/src/uploads/paths.test.ts`, `apps/console/src/uploads/kinds.test.ts`, `apps/console/src/uploads/eta.test.ts`

**Interfaces:**
- Produces:
  - `paths.ts`: `joinPath(...parts: string[]): string`, `parentOf(path: string): string`, `baseName(path: string): string`, `folderNameError(name: string): string | null`, `listingUrl(projectId: string, dir: string): string`, `fileUrl(projectId: string, path: string): string`, `filesRoute(projectId: string, dir: string): string`
  - `kinds.ts`: `type Kind = "dump" | "archive"`, `kindOf(name: string): Kind | null`, `promptFor(kind: Kind, path: string): string`
  - `eta.ts`: `interface Sample { at: number; offset: number }` (`at` in ms), `addSample(samples: readonly Sample[], sample: Sample): Sample[]`, `secondsLeft(samples: readonly Sample[], remaining: number): number | null`, `timeLeftWords(seconds: number): string`

- [ ] **Step 1: Write the failing tests**

`paths.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { fileUrl, filesRoute, folderNameError, joinPath, parentOf } from "./paths";

describe("upload paths", () => {
  it("joins without leading, trailing or double slashes, so the top of the project is ''", () => {
    expect(joinPath("", "a.sql")).toBe("a.sql");
    expect(joinPath("data/", "/dumps", "a.sql")).toBe("data/dumps/a.sql");
    expect(joinPath("")).toBe("");
  });

  it("refuses a new folder name the agent would reject as a traversal or a nested path", () => {
    expect(folderNameError("dumps")).toBeNull();
    expect(folderNameError("  ")).not.toBeNull();
    expect(folderNameError("a/b")).not.toBeNull();
    expect(folderNameError("a\\b")).not.toBeNull();
    expect(folderNameError("..")).not.toBeNull();
    expect(folderNameError(".")).not.toBeNull();
  });

  it("finds the parent folder of a nested and a top-level path", () => {
    expect(parentOf("data/dumps/a.sql")).toBe("data/dumps");
    expect(parentOf("a.sql")).toBe("");
  });

  it("encodes each path segment but keeps the slashes between them", () => {
    expect(fileUrl("recipe-box", "data/my file#1.sql")).toBe("/api/projects/recipe-box/files/data/my%20file%231.sql");
    expect(filesRoute("recipe-box", "")).toBe("/p/recipe-box/files");
    expect(filesRoute("recipe-box", "data/a b")).toBe("/p/recipe-box/files/data/a%20b");
  });
});
```

`kinds.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { kindOf } from "./kinds";

describe("kindOf", () => {
  it("recognises dumps, including the double extension and upper case", () => {
    expect(kindOf("orders-dump.sql")).toBe("dump");
    expect(kindOf("orders.SQL.GZ")).toBe("dump");
    expect(kindOf("prod.dump")).toBe("dump");
    expect(kindOf("site.bak")).toBe("dump");
  });

  it("recognises archives and does not mistake a .sql.gz for one", () => {
    expect(kindOf("media-library.zip")).toBe("archive");
    expect(kindOf("site.tar.gz")).toBe("archive");
    expect(kindOf("site.tgz")).toBe("archive");
  });

  it("gives nothing for other files, no extension, or a disguised name", () => {
    expect(kindOf("README.md")).toBeNull();
    expect(kindOf("Makefile")).toBeNull();
    expect(kindOf("archive.zip.txt")).toBeNull();
  });
});
```

`eta.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { addSample, secondsLeft, timeLeftWords, type Sample } from "./eta";

describe("time left", () => {
  it("says nothing until it has five seconds of samples", () => {
    const samples: Sample[] = [{ at: 0, offset: 0 }, { at: 4000, offset: 4000 }];
    expect(secondsLeft(samples, 1000)).toBeNull();
  });

  it("divides what's left by the speed over the window", () => {
    const samples: Sample[] = [{ at: 0, offset: 0 }, { at: 10_000, offset: 10_000 }];
    expect(secondsLeft(samples, 60_000)).toBe(60);
  });

  it("says nothing when a stall filled the window, instead of 'about 3 days'", () => {
    let samples: Sample[] = [];
    for (let at = 0; at <= 25_000; at += 5000) samples = addSample(samples, { at, offset: 100 });
    expect(secondsLeft(samples, 10_000_000)).toBeNull();
  });

  it("drops samples older than twenty seconds", () => {
    let samples: Sample[] = [];
    for (let at = 0; at <= 30_000; at += 10_000) samples = addSample(samples, { at, offset: at });
    expect(samples.map((s) => s.at)).toEqual([10_000, 20_000, 30_000]);
  });

  it("words minutes and hours the way the board does", () => {
    expect(timeLeftWords(30)).toBe("less than a minute left");
    expect(timeLeftWords(240)).toBe("about 4 minutes left");
    expect(timeLeftWords(90)).toBe("about 2 minutes left");
    expect(timeLeftWords(7200)).toBe("about 2 hours left");
  });
});
```

- [ ] **Step 2: Run to see them fail**

Run: `npx vitest run apps/console/src/uploads`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`paths.ts`:

```ts
import { projectPath } from "../projects/queries";

export function joinPath(...parts: string[]): string {
  return parts.flatMap((part) => part.split("/")).filter(Boolean).join("/");
}

export function parentOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut === -1 ? "" : path.slice(0, cut);
}

export function baseName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

export function folderNameError(name: string): string | null {
  const trimmed = name.trim();
  if (trimmed === "") return "Give the folder a name.";
  if (/[/\\]/.test(trimmed)) return "A folder name can't have / or \\ in it.";
  if (trimmed === "." || trimmed === "..") return "Pick a different name.";
  return null;
}

const encodePath = (path: string) => path.split("/").map(encodeURIComponent).join("/");

export function listingUrl(projectId: string, dir: string): string {
  return `${projectPath(projectId)}/files?dir=${encodeURIComponent(dir)}`;
}

export function fileUrl(projectId: string, path: string): string {
  return `${projectPath(projectId)}/files/${encodePath(path)}`;
}

export function filesRoute(projectId: string, dir: string): string {
  const base = `/p/${encodeURIComponent(projectId)}/files`;
  return dir === "" ? base : `${base}/${encodePath(dir)}`;
}
```

`kinds.ts`:

```ts
export type Kind = "dump" | "archive";

const DUMP = [".sql", ".sql.gz", ".dump", ".bak", ".backup"];
const ARCHIVE = [".zip", ".tar", ".tar.gz", ".tgz"];

export function kindOf(name: string): Kind | null {
  const lower = name.toLowerCase();
  // Dumps first: ".sql.gz" must not fall through to an archive check.
  if (DUMP.some((ext) => lower.endsWith(ext))) return "dump";
  if (ARCHIVE.some((ext) => lower.endsWith(ext))) return "archive";
  return null;
}

export function promptFor(kind: Kind, path: string): string {
  if (kind === "dump") {
    return `I uploaded a database dump to ${path} in this project. Please import it into the project's database using the credentials already configured, then tell me which tables landed and roughly how many rows each one has.`;
  }
  return `I uploaded ${path} to this project. Please unpack it where it belongs in the project, tell me what was inside, and delete the archive once everything is in place.`;
}
```

`eta.ts`:

```ts
export interface Sample {
  at: number;
  offset: number;
}

const WINDOW_MS = 20_000;
const MIN_SPAN_MS = 5000;
const MAX_SECONDS = 86_400;

export function addSample(samples: readonly Sample[], sample: Sample): Sample[] {
  return [...samples, sample].filter((s) => sample.at - s.at <= WINDOW_MS);
}

export function secondsLeft(samples: readonly Sample[], remaining: number): number | null {
  if (samples.length < 2) return null;
  const first = samples[0];
  const last = samples[samples.length - 1];
  const span = last.at - first.at;
  if (span < MIN_SPAN_MS) return null;
  const perSecond = (last.offset - first.offset) / (span / 1000);
  if (perSecond <= 0) return null;
  const seconds = remaining / perSecond;
  return seconds > MAX_SECONDS ? null : seconds;
}

export function timeLeftWords(seconds: number): string {
  if (seconds < 60) return "less than a minute left";
  if (seconds < 3600) {
    const minutes = Math.round(seconds / 60);
    return `about ${minutes} ${minutes === 1 ? "minute" : "minutes"} left`;
  }
  const hours = Math.round(seconds / 3600);
  return `about ${hours} ${hours === 1 ? "hour" : "hours"} left`;
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `npx vitest run apps/console/src/uploads && npm run typecheck`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add apps/console/src/uploads/paths.ts apps/console/src/uploads/paths.test.ts apps/console/src/uploads/kinds.ts apps/console/src/uploads/kinds.test.ts apps/console/src/uploads/eta.ts apps/console/src/uploads/eta.test.ts
git commit -m "Add upload path, file-kind and time-left helpers"
```

---

### Task 3: Upload API and the queue's protocol

**Files:**
- Create: `apps/console/src/uploads/uploadApi.ts`, `apps/console/src/uploads/queue.ts`
- Test: `apps/console/src/uploads/fakeAgent.ts` (test helper, not a test file), `apps/console/src/uploads/queue.test.ts`

**Interfaces:**
- Consumes: `ApiError`, `isSessionLost`, `SessionLoss`, `createApi`, `Api` (Task 1); `joinPath`, `parentOf`, `baseName` (Task 2); `addSample`, `Sample` (Task 2); `projectPath` from `projects/queries.ts`.
- Produces (`uploadApi.ts`):
  ```ts
  interface StartBody { path: string; size: number; fingerprint: string; replace: boolean }
  interface StartAnswer { upload_id: string; offset: number; size: number; chunk_size?: number; done: boolean }
  interface ChunkAnswer { upload_id: string; offset: number; size: number; done: boolean }
  interface PendingUpload { id: string; project_id: string; path: string; size: number; offset: number; fingerprint: string; replace: boolean; updated_at: number }
  interface Disk { free_bytes: number; total_bytes: number }
  interface UploadApi {
    start(projectId: string, body: StartBody): Promise<StartAnswer>;
    patch(uploadId: string, offset: number, chunk: Blob, signal: AbortSignal): Promise<ChunkAnswer>;
    status(uploadId: string): Promise<PendingUpload>;
    cancel(uploadId: string): Promise<unknown>;
    pending(projectId: string): Promise<{ uploads: PendingUpload[] }>;
    disk(): Promise<Disk>;
  }
  function createUploadApi(client?: Api): UploadApi
  ```
- Produces (`queue.ts`):
  ```ts
  const RESERVE = 1024 ** 3; const DEFAULT_CHUNK = 8 * 1024 * 1024;
  type UploadState = "waiting" | "going" | "paused" | "stalled" | "noRoom" | "failed" | "done";
  interface UploadItem {
    key: string; projectId: string; dir: string; name: string; size: number; fingerprint: string;
    file: File | null; uploadId: string | null; offset: number; chunkSize: number; replace: boolean;
    state: UploadState; reason: string | null; message: string | null; busy: boolean;
    fromReload: boolean; freeBytes: number | null; samples: readonly Sample[];
  }
  function fingerprintOf(file: { name: string; size: number; lastModified: number }): string  // "name:size:lastModified"
  function fits(size: number, freeBytes: number): boolean  // size + RESERVE <= freeBytes
  class UploadQueue {
    constructor(options: { api: UploadApi; onLanded?: (item: UploadItem) => void;
      onSessionLost?: (reason: SessionLoss) => void; sleep?: (ms: number) => Promise<void>; now?: () => number })
    subscribe(listener: () => void): () => void   // arrow property
    snapshot(): readonly UploadItem[]            // arrow property; new array on every change
    settled(): Promise<void>                     // resolves when the runner goes idle
    add(projectId: string, dir: string, files: readonly File[]): string[]
    // Task 4 adds: pause, resume, remove, replace, relink, adoptPending, syncPending, carryOn
  }
  ```

- [ ] **Step 1: Write the upload API**

`uploadApi.ts`:

```ts
import { createApi, type Api } from "../api/client";
import { projectPath } from "../projects/queries";

export interface StartBody {
  path: string;
  size: number;
  fingerprint: string;
  replace: boolean;
}

export interface StartAnswer {
  upload_id: string;
  offset: number;
  size: number;
  chunk_size?: number;
  done: boolean;
}

export interface ChunkAnswer {
  upload_id: string;
  offset: number;
  size: number;
  done: boolean;
}

export interface PendingUpload {
  id: string;
  project_id: string;
  path: string;
  size: number;
  offset: number;
  fingerprint: string;
  replace: boolean;
  updated_at: number;
}

export interface Disk {
  free_bytes: number;
  total_bytes: number;
}

export interface UploadApi {
  start(projectId: string, body: StartBody): Promise<StartAnswer>;
  patch(uploadId: string, offset: number, chunk: Blob, signal: AbortSignal): Promise<ChunkAnswer>;
  status(uploadId: string): Promise<PendingUpload>;
  cancel(uploadId: string): Promise<unknown>;
  pending(projectId: string): Promise<{ uploads: PendingUpload[] }>;
  disk(): Promise<Disk>;
}

const uploadPath = (id: string) => `/api/uploads/${encodeURIComponent(id)}`;

// The shared client gives up after 10 s; an 8 MiB chunk on a slow link needs longer.
export function createUploadApi(client: Api = createApi((input, init) => fetch(input, init), { timeoutMs: 120_000 })): UploadApi {
  return {
    start: (projectId, body) => client.post(`${projectPath(projectId)}/uploads`, body),
    patch: (id, offset, chunk, signal) => client.patch(uploadPath(id), chunk, { "Upload-Offset": String(offset) }, signal),
    status: (id) => client.get(uploadPath(id)),
    cancel: (id) => client.del(uploadPath(id)),
    pending: (projectId) => client.get(`${projectPath(projectId)}/uploads`),
    disk: () => client.get("/api/disk"),
  };
}
```

- [ ] **Step 2: Write the fake agent test helper**

`fakeAgent.ts` — an in-memory agent that follows the real protocol (offset check, append, finish), with hooks to inject failures. Chunk size is 4 bytes so a 10-byte file takes three chunks.

```ts
import { ApiError } from "../api/client";
import type { PendingUpload, UploadApi } from "./uploadApi";

type Method = "start" | "patch" | "status";

export function fakeAgent() {
  const calls: string[] = [];
  const sleeps: number[] = [];
  const uploads = new Map<string, { size: number; offset: number; path: string }>();
  const counts: Record<Method, number> = { start: 0, patch: 0, status: 0 };
  const scripted: Array<{ method: Method; at: number; error: ApiError }> = [];
  const state = { next: 0, free: 100 * 1024 ** 3, skew: 0, busyOnFinish: 0, hangAt: 0 };

  function scriptedFailure(method: Method): void {
    counts[method] += 1;
    const hit = scripted.find((s) => s.method === method && s.at === counts[method]);
    if (hit) throw hit.error;
  }

  const api: UploadApi = {
    async start(_projectId, body) {
      calls.push(`start ${body.path}${body.replace ? " replace" : ""}`);
      scriptedFailure("start");
      const id = `up${++state.next}`;
      uploads.set(id, { size: body.size, offset: state.skew, path: body.path });
      return { upload_id: id, offset: 0, size: body.size, chunk_size: 4, done: body.size === 0 };
    },
    patch(id, offset, chunk, signal) {
      calls.push(`patch ${id} @${offset}+${chunk.size}`);
      try {
        scriptedFailure("patch");
      } catch (error) {
        return Promise.reject(error);
      }
      if (state.hangAt === counts.patch) {
        return new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new ApiError("aborted", "cancelled", 0)));
        });
      }
      const up = uploads.get(id);
      if (!up) return Promise.reject(new ApiError("upload_not_found", "no such upload", 404));
      if (offset !== up.offset) return Promise.reject(new ApiError("offset_mismatch", "m", 409, { offset: up.offset }));
      up.offset += chunk.size;
      if (up.offset === up.size && state.busyOnFinish > 0) {
        state.busyOnFinish -= 1;
        return Promise.reject(new ApiError("project_busy", "busy", 409));
      }
      if (up.offset === up.size) uploads.delete(id);
      return Promise.resolve({ upload_id: id, offset: up.offset, size: up.size, done: up.offset === up.size });
    },
    async status(id) {
      calls.push(`status ${id}`);
      scriptedFailure("status");
      const up = uploads.get(id);
      if (!up) throw new ApiError("upload_not_found", "no such upload", 404);
      const pending: PendingUpload = {
        id, project_id: "p", path: up.path, size: up.size, offset: up.offset, fingerprint: "", replace: false, updated_at: 0,
      };
      return pending;
    },
    async cancel(id) {
      calls.push(`cancel ${id}`);
      uploads.delete(id);
      return {};
    },
    async pending() {
      return { uploads: [] };
    },
    async disk() {
      return { free_bytes: state.free, total_bytes: 200 * 1024 ** 3 };
    },
  };

  return {
    api,
    calls,
    sleeps,
    uploads,
    state,
    failOn(method: Method, at: number, error: ApiError) {
      scripted.push({ method, at, error });
    },
    sleep: (ms: number) => {
      sleeps.push(ms);
      return Promise.resolve();
    },
  };
}

export function file(name: string, contents: string, lastModified = 1): File {
  return new File([contents], name, { lastModified });
}

export async function until(check: () => boolean): Promise<void> {
  for (let i = 0; i < 200; i += 1) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error("condition never became true");
}
```

Note: `fakeAgent.ts` doesn't match the `*.test.ts` include, so Vitest never runs it as a suite; it is still typechecked with the app.

- [ ] **Step 3: Write the failing protocol tests**

`queue.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { fakeAgent, file } from "./fakeAgent";
import { UploadQueue, type UploadItem } from "./queue";

function setup() {
  const agent = fakeAgent();
  const landed: UploadItem[] = [];
  const lost: string[] = [];
  const queue = new UploadQueue({
    api: agent.api,
    sleep: agent.sleep,
    onLanded: (item) => landed.push(item),
    onSessionLost: (reason) => lost.push(reason),
  });
  const item = (key: string) => queue.snapshot().find((i) => i.key === key)!;
  return { agent, queue, landed, lost, item };
}

describe("UploadQueue protocol", () => {
  it("sends a file in chunk_size slices, in order, and lands it", async () => {
    const { agent, queue, landed, item } = setup();
    const [key] = queue.add("p", "data", [file("a.sql", "abcdefghij")]);
    await queue.settled();
    expect(agent.calls).toEqual(["start data/a.sql", "patch up1 @0+4", "patch up1 @4+4", "patch up1 @8+2"]);
    expect(item(key).state).toBe("done");
    expect(landed.map((i) => i.name)).toEqual(["a.sql"]);
  });

  it("carries on from the offset the agent reports after an offset_mismatch", async () => {
    const { agent, queue, item } = setup();
    agent.state.skew = 4;
    const [key] = queue.add("p", "", [file("a.txt", "abcdefghij")]);
    await queue.settled();
    expect(agent.calls).toEqual(["start a.txt", "patch up1 @0+4", "patch up1 @4+4", "patch up1 @8+2"]);
    expect(item(key).state).toBe("done");
  });

  it("retries an empty PATCH at the full size while the project is busy, until it lands", async () => {
    const { agent, queue, item } = setup();
    agent.state.busyOnFinish = 2;
    const [key] = queue.add("p", "", [file("a.txt", "abcdefghij")]);
    await queue.settled();
    expect(agent.calls.slice(-3)).toEqual(["patch up1 @8+2", "patch up1 @10+0", "patch up1 @10+0"]);
    expect(agent.sleeps).toEqual([3000, 3000]);
    expect(item(key).state).toBe("done");
  });

  it("stops at the agent's offset on disk_full and starts nothing else", async () => {
    const { agent, queue, item } = setup();
    agent.failOn("patch", 2, new ApiError("disk_full", "full", 507, { offset: 4 }));
    const [a, b] = queue.add("p", "", [file("a.txt", "abcdefghij"), file("b.txt", "xyz")]);
    await queue.settled();
    expect(item(a)).toMatchObject({ state: "noRoom", offset: 4 });
    expect(item(b).state).toBe("waiting");
    expect(agent.calls.some((c) => c.startsWith("start b.txt"))).toBe(false);
  });

  it("sends one file at a time, in the order they were added", async () => {
    const { agent, queue } = setup();
    queue.add("p", "", [file("a.txt", "abcde"), file("b.txt", "xy")]);
    await queue.settled();
    expect(agent.calls).toEqual(["start a.txt", "patch up1 @0+4", "patch up1 @4+1", "start b.txt", "patch up2 @0+2"]);
  });

  it("marks an upload stalled after three failed status reads on a dropped connection", async () => {
    const { agent, queue, item } = setup();
    const gone = new ApiError("unreachable", "down", 0);
    agent.failOn("patch", 1, gone);
    agent.failOn("status", 1, gone);
    agent.failOn("status", 2, gone);
    agent.failOn("status", 3, gone);
    const [key] = queue.add("p", "", [file("a.txt", "abcdefghij")]);
    await queue.settled();
    expect(agent.sleeps).toEqual([2000, 4000, 8000]);
    expect(item(key)).toMatchObject({ state: "stalled", offset: 0 });
  });

  it("restarts from zero once when the agent lost the upload, then gives up", async () => {
    const { agent, queue, item } = setup();
    const lost = new ApiError("upload_not_found", "no such upload", 404);
    agent.failOn("patch", 1, lost);
    agent.failOn("patch", 2, lost);
    const [key] = queue.add("p", "", [file("a.txt", "abcdefghij")]);
    await queue.settled();
    expect(agent.calls).toEqual(["start a.txt", "patch up1 @0+4", "start a.txt", "patch up2 @0+4"]);
    expect(item(key)).toMatchObject({ state: "failed", reason: "upload_not_found" });
  });

  it("fails a start the agent refuses and keeps going with the next file", async () => {
    const { agent, queue, item } = setup();
    agent.failOn("start", 1, new ApiError("file_exists", "'a.txt' is already in the project", 409));
    const [a, b] = queue.add("p", "", [file("a.txt", "abc"), file("b.txt", "xy")]);
    await queue.settled();
    expect(item(a)).toMatchObject({ state: "failed", reason: "file_exists" });
    expect(item(b).state).toBe("done");
  });

  it("hands a lost session to the app instead of failing the upload", async () => {
    const { agent, queue, lost, item } = setup();
    agent.failOn("patch", 1, new ApiError("session_expired", "gone", 401));
    const [key] = queue.add("p", "", [file("a.txt", "abcdefghij")]);
    await queue.settled();
    expect(lost).toEqual(["session_expired"]);
    expect(item(key).state).toBe("stalled");
  });
});
```

- [ ] **Step 4: Run to see them fail**

Run: `npx vitest run apps/console/src/uploads/queue.test.ts`
Expected: FAIL — `./queue` not found.

- [ ] **Step 5: Implement `queue.ts`**

```ts
import { ApiError, isSessionLost, type SessionLoss } from "../api/client";
import { addSample, type Sample } from "./eta";
import { joinPath } from "./paths";
import type { UploadApi } from "./uploadApi";

export const RESERVE = 1024 ** 3;
export const DEFAULT_CHUNK = 8 * 1024 * 1024;
const BUSY_RETRY_MS = 3000;
const NETWORK_RETRIES = 3;

export type UploadState = "waiting" | "going" | "paused" | "stalled" | "noRoom" | "failed" | "done";

export interface UploadItem {
  key: string;
  projectId: string;
  dir: string;
  name: string;
  size: number;
  fingerprint: string;
  file: File | null;
  uploadId: string | null;
  offset: number;
  chunkSize: number;
  replace: boolean;
  state: UploadState;
  reason: string | null;
  message: string | null;
  busy: boolean;
  fromReload: boolean;
  freeBytes: number | null;
  samples: readonly Sample[];
}

export function fingerprintOf(file: { name: string; size: number; lastModified: number }): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

export function fits(size: number, freeBytes: number): boolean {
  return size + RESERVE <= freeBytes;
}

function numberOr<T>(value: unknown, fallback: T): number | T {
  return typeof value === "number" ? value : fallback;
}

export interface QueueOptions {
  api: UploadApi;
  onLanded?: (item: UploadItem) => void;
  onSessionLost?: (reason: SessionLoss) => void;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

export class UploadQueue {
  private items: readonly UploadItem[] = [];
  private readonly listeners = new Set<() => void>();
  private active: { key: string; controller: AbortController } | null = null;
  private running = false;
  private runPromise: Promise<void> | null = null;
  private counter = 0;
  private readonly api: UploadApi;
  private readonly onLanded: (item: UploadItem) => void;
  private readonly onSessionLost: (reason: SessionLoss) => void;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;

  constructor(options: QueueOptions) {
    this.api = options.api;
    this.onLanded = options.onLanded ?? (() => {});
    this.onSessionLost = options.onSessionLost ?? (() => {});
    this.sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.now = options.now ?? (() => Date.now());
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  snapshot = (): readonly UploadItem[] => this.items;

  settled(): Promise<void> {
    return this.runPromise ?? Promise.resolve();
  }

  add(projectId: string, dir: string, files: readonly File[]): string[] {
    const added: UploadItem[] = files.map((f) => ({
      key: `u${++this.counter}`,
      projectId,
      dir,
      name: f.name,
      size: f.size,
      fingerprint: fingerprintOf(f),
      file: f,
      uploadId: null,
      offset: 0,
      chunkSize: DEFAULT_CHUNK,
      replace: false,
      state: "waiting",
      reason: null,
      message: null,
      busy: false,
      fromReload: false,
      freeBytes: null,
      samples: [],
    }));
    this.items = [...this.items, ...added];
    this.emit();
    this.kick();
    return added.map((i) => i.key);
  }

  // A disk_full item already holds staged bytes; nothing else may start until
  // space is freed, or it would hit the same wall.
  private get held(): boolean {
    return this.items.some((i) => i.state === "noRoom" && i.uploadId !== null);
  }

  private kick(): void {
    if (this.running) return;
    this.running = true;
    this.runPromise = this.run();
  }

  private async run(): Promise<void> {
    // `running` is cleared in the same synchronous step that finds no work,
    // so an add() arriving right after can never be missed.
    try {
      for (;;) {
        if (this.held) return;
        const next = this.items.find((i) => i.state === "waiting");
        if (!next) return;
        await this.send(next.key);
      }
    } finally {
      this.running = false;
    }
  }

  private async send(key: string): Promise<void> {
    const controller = new AbortController();
    this.active = { key, controller };
    this.update(key, { state: "going", reason: null, message: null, busy: false, samples: [] });
    let needSync = this.find(key)?.uploadId != null;
    let restarted = false;
    let failures = 0;
    try {
      for (;;) {
        const item = this.find(key);
        if (!item || item.state !== "going") return;
        try {
          if (item.uploadId === null) {
            const answer = await this.api.start(item.projectId, {
              path: joinPath(item.dir, item.name),
              size: item.size,
              fingerprint: item.fingerprint,
              replace: item.replace,
            });
            failures = 0;
            if (!this.find(key)) {
              if (!answer.done) void this.api.cancel(answer.upload_id).catch(() => {});
              return;
            }
            if (answer.done) {
              this.land(key);
              return;
            }
            this.update(key, { uploadId: answer.upload_id, offset: answer.offset, chunkSize: answer.chunk_size ?? item.chunkSize });
            continue;
          }
          if (needSync) {
            const status = await this.api.status(item.uploadId);
            needSync = false;
            failures = 0;
            this.update(key, { offset: status.offset });
            continue;
          }
          if (item.file === null) {
            this.update(key, { state: "stalled" });
            return;
          }
          const end = Math.min(item.size, item.offset + item.chunkSize);
          const answer = await this.api.patch(item.uploadId, item.offset, item.file.slice(item.offset, end), controller.signal);
          failures = 0;
          if (answer.done) {
            this.land(key);
            return;
          }
          this.update(key, {
            offset: answer.offset,
            busy: false,
            samples: addSample(item.samples, { at: this.now(), offset: answer.offset }),
          });
        } catch (error) {
          if (!(error instanceof ApiError)) throw error;
          if (error.code === "aborted") return;
          if (isSessionLost(error)) {
            this.update(key, { state: "stalled" });
            this.onSessionLost(error.code);
            return;
          }
          switch (error.code) {
            case "offset_mismatch":
              this.update(key, { offset: numberOr(error.details.offset, item.offset) });
              continue;
            case "project_busy":
              // Only the finishing step takes the project lock, so every byte is already in.
              this.update(key, { busy: true, offset: item.size });
              await this.sleep(BUSY_RETRY_MS);
              continue;
            case "disk_full":
              this.update(key, { state: "noRoom", offset: numberOr(error.details.offset, item.offset) });
              return;
            case "not_enough_space":
              this.update(key, { state: "noRoom", freeBytes: numberOr(error.details.free_bytes, null) });
              return;
            case "unreachable":
              failures += 1;
              if (failures > NETWORK_RETRIES) {
                this.update(key, { state: "stalled" });
                return;
              }
              await this.sleep(2000 * 2 ** (failures - 1));
              needSync = item.uploadId !== null;
              continue;
            case "upload_not_found":
              if (restarted) {
                this.fail(key, error);
                return;
              }
              restarted = true;
              this.update(key, { uploadId: null, offset: 0, samples: [] });
              continue;
            default:
              this.fail(key, error);
              return;
          }
        }
      }
    } finally {
      if (this.active?.controller === controller) this.active = null;
    }
  }

  private land(key: string): void {
    const item = this.find(key);
    if (!item) return;
    this.update(key, { state: "done", offset: item.size, busy: false, file: null });
    this.onLanded(this.find(key)!);
  }

  private fail(key: string, error: ApiError): void {
    this.update(key, { state: "failed", reason: error.code, message: error.message });
  }

  private find(key: string): UploadItem | undefined {
    return this.items.find((i) => i.key === key);
  }

  private update(key: string, patch: Partial<UploadItem>): void {
    if (!this.find(key)) return;
    this.items = this.items.map((i) => (i.key === key ? { ...i, ...patch } : i));
    this.emit();
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }
}
```

- [ ] **Step 6: Run the tests and typecheck**

Run: `npx vitest run apps/console/src/uploads && npm run typecheck`
Expected: all PASS; clean.

- [ ] **Step 7: Commit**

```bash
git add apps/console/src/uploads/uploadApi.ts apps/console/src/uploads/queue.ts apps/console/src/uploads/fakeAgent.ts apps/console/src/uploads/queue.test.ts
git commit -m "Add the upload queue: chunked, resumable, one file at a time"
```

---

### Task 4: Queue controls — pause, resume, remove, replace, relink, reload recovery, carry on

**Files:**
- Modify: `apps/console/src/uploads/queue.ts`
- Test: `apps/console/src/uploads/queue.test.ts` (append)

**Interfaces:**
- Consumes: everything from Task 3; `parentOf`, `baseName` (Task 2); `PendingUpload` (Task 3).
- Produces, as `UploadQueue` methods:
  - `pause(key: string): void` — only a `going` item; aborts its chunk, state `paused`.
  - `resume(key: string): void` — a `paused` item, or a `stalled` item that has a `file`; state `waiting`. The runner re-reads the server offset before the next chunk (Task 3's `needSync`).
  - `remove(key: string): void` — aborts if active, drops the item, cancels on the agent when it has an `uploadId` and isn't `done` (a failed cancel is ignored).
  - `replace(key: string): void` — a `failed` item with `reason === "file_exists"`: `replace: true`, `waiting`.
  - `relink(key: string, file: File): boolean` — false (and nothing changes) when `fingerprintOf(file) !== item.fingerprint`; otherwise sets the file, `waiting`.
  - `adoptPending(projectId: string, uploads: readonly PendingUpload[]): void` — each upload whose `id` no item holds as `uploadId` becomes a `stalled`, `fromReload: true` item with `file: null`, `key` = the upload id.
  - `syncPending(projectId: string): Promise<void>` — `api.pending` then `adoptPending`; a lost session goes to `onSessionLost`; other errors are ignored.
  - `carryOn(): Promise<boolean>` — reads `api.disk()`; each `noRoom` item moves to `waiting` when its need fits (with an `uploadId`: `size - offset <= free`; without: `fits(size, free)`), otherwise its `freeBytes` is updated. Returns whether anything moved.

- [ ] **Step 1: Write the failing tests** — append to `queue.test.ts` (add `until` to the `./fakeAgent` import and `type PendingUpload` from `./uploadApi`):

```ts
describe("UploadQueue controls", () => {
  it("pauses by aborting the chunk and re-reads the server offset before resuming", async () => {
    const { agent, queue, item } = setup();
    agent.state.hangAt = 2;
    const [key] = queue.add("p", "", [file("a.txt", "abcdefghij")]);
    await until(() => agent.calls.length === 3);
    queue.pause(key);
    await queue.settled();
    expect(item(key)).toMatchObject({ state: "paused", offset: 4 });
    agent.state.hangAt = 0;
    queue.resume(key);
    await queue.settled();
    expect(agent.calls.slice(3)).toEqual(["status up1", "patch up1 @4+4", "patch up1 @8+2"]);
    expect(item(key).state).toBe("done");
  });

  it("cancels a started upload on the agent when it is removed, then moves on", async () => {
    const { agent, queue } = setup();
    agent.state.hangAt = 1;
    const [a] = queue.add("p", "", [file("a.txt", "abcdefghij"), file("b.txt", "xy")]);
    await until(() => agent.calls.length === 2);
    queue.remove(a);
    await until(() => queue.snapshot().every((i) => i.state === "done"));
    expect(agent.calls).toContain("cancel up1");
    expect(queue.snapshot().map((i) => i.name)).toEqual(["b.txt"]);
  });

  it("resends a refused duplicate with replace once the user says so", async () => {
    const { agent, queue, item } = setup();
    agent.failOn("start", 1, new ApiError("file_exists", "exists", 409));
    const [key] = queue.add("p", "data", [file("a.txt", "abc")]);
    await queue.settled();
    queue.replace(key);
    await queue.settled();
    expect(agent.calls).toContain("start data/a.txt replace");
    expect(item(key).state).toBe("done");
  });

  const pending = (over: Partial<PendingUpload> = {}): PendingUpload => ({
    id: "srv1", project_id: "p", path: "data/a.txt", size: 10, offset: 4,
    fingerprint: "a.txt:10:7", replace: false, updated_at: 0, ...over,
  });

  it("refuses to resume a reloaded upload with a different file", async () => {
    const { queue, item } = setup();
    queue.adoptPending("p", [pending()]);
    expect(queue.relink("srv1", file("a.txt", "abcdefghij", 8))).toBe(false);
    expect(item("srv1")).toMatchObject({ state: "stalled", file: null });
  });

  it("resumes a reloaded upload from the server's offset once given the same file", async () => {
    const { agent, queue, item } = setup();
    agent.uploads.set("srv1", { size: 10, offset: 4, path: "data/a.txt" });
    queue.adoptPending("p", [pending()]);
    expect(item("srv1")).toMatchObject({ dir: "data", name: "a.txt", fromReload: true });
    expect(queue.relink("srv1", file("a.txt", "abcdefghij", 7))).toBe(true);
    await queue.settled();
    // No start answer after a reload, so the chunk size is the 8 MiB default: one PATCH finishes it.
    expect(agent.calls).toEqual(["status srv1", "patch srv1 @4+6"]);
    expect(item("srv1").state).toBe("done");
  });

  it("doesn't add a second row for an upload it already holds", () => {
    const { queue } = setup();
    queue.adoptPending("p", [pending()]);
    queue.adoptPending("p", [pending()]);
    expect(queue.snapshot()).toHaveLength(1);
  });

  it("carries on after disk_full only once the agent reports room", async () => {
    const { agent, queue, item } = setup();
    agent.failOn("patch", 2, new ApiError("disk_full", "full", 507, { offset: 4 }));
    const [a, b] = queue.add("p", "", [file("a.txt", "abcdefghij"), file("b.txt", "xy")]);
    await queue.settled();
    agent.state.free = 3;
    expect(await queue.carryOn()).toBe(false);
    expect(item(a).state).toBe("noRoom");
    agent.state.free = 100;
    expect(await queue.carryOn()).toBe(true);
    await until(() => item(b).state === "done");
    expect(item(a).state).toBe("done");
  });
});
```

Note on the disk_full test: the scripted failure fires before the fake appends, so the fake agent's own offset for `up1` stays at 4 — matching `details.offset` — and resume continues at 4.

- [ ] **Step 2: Run to see them fail**

Run: `npx vitest run apps/console/src/uploads/queue.test.ts`
Expected: the new tests FAIL (`queue.pause is not a function`, …).

- [ ] **Step 3: Implement** — add to `UploadQueue` (after `add`), and extend the imports: `import { baseName, joinPath, parentOf } from "./paths";` and `import type { PendingUpload, UploadApi } from "./uploadApi";`

```ts
  pause(key: string): void {
    if (this.find(key)?.state !== "going") return;
    this.update(key, { state: "paused", busy: false });
    this.abort(key);
  }

  resume(key: string): void {
    const item = this.find(key);
    if (!item) return;
    const resumable = item.state === "paused" || (item.state === "stalled" && item.file !== null);
    if (!resumable) return;
    this.update(key, { state: "waiting" });
    this.kick();
  }

  remove(key: string): void {
    const item = this.find(key);
    if (!item) return;
    this.abort(key);
    this.items = this.items.filter((i) => i.key !== key);
    this.emit();
    // A failed cancel is left for the agent's seven-day sweep.
    if (item.uploadId !== null && item.state !== "done") void this.api.cancel(item.uploadId).catch(() => {});
    this.kick();
  }

  replace(key: string): void {
    const item = this.find(key);
    if (item?.state !== "failed" || item.reason !== "file_exists") return;
    this.update(key, { replace: true, state: "waiting", reason: null, message: null });
    this.kick();
  }

  relink(key: string, file: File): boolean {
    const item = this.find(key);
    if (!item || fingerprintOf(file) !== item.fingerprint) return false;
    this.update(key, { file, state: "waiting" });
    this.kick();
    return true;
  }

  adoptPending(projectId: string, uploads: readonly PendingUpload[]): void {
    const held = new Set(this.items.map((i) => i.uploadId));
    const found: UploadItem[] = uploads
      .filter((u) => !held.has(u.id))
      .map((u) => ({
        key: u.id,
        projectId,
        dir: parentOf(u.path),
        name: baseName(u.path),
        size: u.size,
        fingerprint: u.fingerprint,
        file: null,
        uploadId: u.id,
        offset: u.offset,
        chunkSize: DEFAULT_CHUNK,
        replace: u.replace,
        state: "stalled",
        reason: null,
        message: null,
        busy: false,
        fromReload: true,
        freeBytes: null,
        samples: [],
      }));
    if (found.length === 0) return;
    this.items = [...this.items, ...found];
    this.emit();
  }

  async syncPending(projectId: string): Promise<void> {
    try {
      const { uploads } = await this.api.pending(projectId);
      this.adoptPending(projectId, uploads);
    } catch (error) {
      if (isSessionLost(error)) this.onSessionLost(error.code);
    }
  }

  async carryOn(): Promise<boolean> {
    const { free_bytes: free } = await this.api.disk();
    let moved = false;
    for (const item of this.items) {
      if (item.state !== "noRoom") continue;
      // Staged bytes are already on disk; only a fresh start needs the reserve.
      const room = item.uploadId !== null ? item.size - item.offset <= free : fits(item.size, free);
      if (room) {
        this.update(item.key, { state: "waiting", freeBytes: null });
        moved = true;
      } else {
        this.update(item.key, { freeBytes: free });
      }
    }
    this.kick();
    return moved;
  }

  private abort(key: string): void {
    if (this.active?.key === key) this.active.controller.abort();
  }
```

- [ ] **Step 4: Run tests and typecheck**

Run: `npx vitest run apps/console/src/uploads && npm run typecheck`
Expected: all PASS; clean. Also run the full suite once: `npm test`.

- [ ] **Step 5: Commit**

```bash
git add apps/console/src/uploads/queue.ts apps/console/src/uploads/queue.test.ts
git commit -m "Let uploads pause, resume, be removed, replaced and picked up after a reload"
```

---

### Task 5: Mock agent — file tree, disk, uploads, download, scenarios

**Files:**
- Modify: `apps/console/src/mocks/handlers.ts`

**Interfaces:**
- Consumes: `joinPath`, `parentOf`, `baseName` (Task 2).
- Produces: `SCENARIOS` gains `"uploads" | "full" | "fills-up" | "busy" | "locked"`; all five start signed in. Routes: `GET /api/disk`, `GET /api/projects/:id/files` (with `dir`), `GET /api/projects/:id/files/*` (download), `POST/GET /api/projects/:id/uploads`, `GET/PATCH/DELETE /api/uploads/:uploadId`.

No tests (the mock is dev tooling; the spec leaves it untested on purpose).

- [ ] **Step 1: Extend scenarios and sign-in**

```ts
export const SCENARIOS = [
  "ok", "empty", "expired", "handoff-spent", "old-agent", "down", "lost-mid-use", "wrong-host",
  "uploads", "full", "fills-up", "busy", "locked",
] as const;
```

and in `handlersFor`:

```ts
  let signedIn = !["expired", "handoff-spent", "down", "wrong-host"].includes(scenario);
```

(Check: this keeps `ok`, `empty`, `old-agent`, `lost-mid-use` signed in exactly as before.)

- [ ] **Step 2: Add the file tree, disk and upload state** — inside `handlersFor`, after the `discovered.push(...)` block:

```ts
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
```

- [ ] **Step 3: Add the handlers** — append to the returned array:

```ts
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
        return refuse("permission_denied", "Eggie can't look inside that folder; a program in the project owns it.", 409);
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
        return HttpResponse.json({ error: { code: "not_enough_space", message: "this file is bigger than the room Eggie has left", free_bytes: disk.free } }, { status: 507 });
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
        return HttpResponse.json({ error: { code: "disk_full", message: "Eggie ran out of room", offset: up.offset } }, { status: 507 });
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
```

Caveat on the `fills-up` one-shot: `disk.free = 0` then resetting via `setTimeout(0)` means `/disk` shows room by the time the user clicks "I've freed some up". That is the intended walk.

Also drop a deleted project's files and uploads: in the existing `http.delete("/api/projects/:id")` handler, after `projects.delete(target.id);`, add `trees.delete(target.id);` and `for (const u of [...uploads.values()]) if (u.project_id === target.id) uploads.delete(u.id);`. Step 2's declarations sit before the `return [`, so the handler can see them.

- [ ] **Step 4: Typecheck, build, test**

Run: `npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: clean; all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/console/src/mocks/handlers.ts
git commit -m "Give the mock agent files, free space, uploads and the part D scenarios"
```

---

### Task 6: Queue provider, Files route, browsing and download

**Files:**
- Create: `apps/console/src/uploads/QueueProvider.tsx`, `apps/console/src/uploads/queries.ts`, `apps/console/src/screens/files/FilesPage.tsx`, `apps/console/src/screens/files/Listing.tsx`, `apps/console/src/screens/files/FilesPage.module.css`
- Modify: `apps/console/src/App.tsx`, `apps/console/src/screens/project/Tiles.tsx`, `apps/console/src/screens/project/ProjectPage.tsx`, `apps/console/src/screens/project/ProjectPage.module.css` (only if the tile link needs `text-decoration: none`)

**Interfaces:**
- Consumes: `UploadQueue`, `UploadItem`, `createUploadApi` (Tasks 3–4); `kindOf` (Task 2); `listingUrl`, `fileUrl`, `filesRoute`, `joinPath` (Task 2); `api`, `ApiError`, `SessionLoss`; `size`, `relativeTime` from `projects/format.ts`; `useNow` from `projects/useNow.ts` (read it for its signature before using).
- Produces:
  - `queries.ts`: `interface DirEntry { name: string; kind: "file" | "folder"; size: number | null; items: number | null; modified: number }`, `interface Listing { dir: string; entries: DirEntry[] }`, `FILES(id: string)` = `["files", id] as const`, `useListing(id: string, dir: string)`, `useDisk(enabled: boolean)` (`["disk"]`, `staleTime: 0`, `gcTime: 0`, `queryFn: () => api.get<Disk>("/api/disk")`).
  - `QueueProvider({ onSessionLost, children })` and `useUploads(projectId: string): { queue: UploadQueue; items: readonly UploadItem[]; landed: UploadItem | null; dismiss: () => void }` — `items` filtered to the project; `landed` only when it belongs to the project.

- [ ] **Step 1: `uploads/queries.ts`**

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { listingUrl } from "./paths";
import type { Disk } from "./uploadApi";

export interface DirEntry {
  name: string;
  kind: "file" | "folder";
  size: number | null;
  items: number | null;
  modified: number;
}

export interface Listing {
  dir: string;
  entries: DirEntry[];
}

export const FILES = (id: string) => ["files", id] as const;

export function useListing(id: string, dir: string) {
  return useQuery({
    queryKey: [...FILES(id), dir],
    queryFn: () => api.get<Listing>(listingUrl(id, dir)),
    refetchOnWindowFocus: true,
  });
}

export function useDisk(enabled: boolean) {
  return useQuery({
    queryKey: ["disk"],
    queryFn: () => api.get<Disk>("/api/disk"),
    enabled,
    staleTime: 0,
    gcTime: 0,
  });
}
```

- [ ] **Step 2: `uploads/QueueProvider.tsx`**

```tsx
import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { SessionLoss } from "../api/client";
import { kindOf } from "./kinds";
import { FILES } from "./queries";
import { UploadQueue, type UploadItem } from "./queue";
import { createUploadApi } from "./uploadApi";

interface QueueContext {
  queue: UploadQueue;
  items: readonly UploadItem[];
  landed: UploadItem | null;
  dismiss: () => void;
}

const Context = createContext<QueueContext | null>(null);

export function QueueProvider({ onSessionLost, children }: { onSessionLost: (reason: SessionLoss) => void; children: ReactNode }) {
  const client = useQueryClient();
  const [landed, setLanded] = useState<UploadItem | null>(null);
  const [queue] = useState(
    () =>
      new UploadQueue({
        api: createUploadApi(),
        onSessionLost,
        onLanded: (item) => {
          void client.invalidateQueries({ queryKey: FILES(item.projectId) });
          if (kindOf(item.name)) setLanded(item);
        },
      }),
  );
  const items = useSyncExternalStore(queue.subscribe, queue.snapshot);
  const going = items.some((item) => item.state === "going");

  useEffect(() => {
    if (!going) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [going]);

  const value = useMemo(() => ({ queue, items, landed, dismiss: () => setLanded(null) }), [queue, items, landed]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useUploads(projectId: string) {
  const context = useContext(Context);
  if (!context) throw new Error("useUploads needs a QueueProvider");
  const { queue, items, landed, dismiss } = context;
  const mine = useMemo(() => items.filter((item) => item.projectId === projectId), [items, projectId]);
  return { queue, items: mine, landed: landed?.projectId === projectId ? landed : null, dismiss };
}
```

- [ ] **Step 3: Wire `App.tsx`** — import `QueueProvider` and `FilesPage`; in the `signedIn` case wrap `<BrowserRouter>` inside `<QueueProvider onSessionLost={(reason) => setResult({ kind: "signedOut", reason })}>` (inside `QueryClientProvider`), and add the route after `/p/:id`:

```tsx
                <Route path="/p/:id/files/*" element={<FilesPage />} />
```

Verify in the browser that `/p/recipe-box/files` (no trailing segment) matches this route; if it does not with React Router 8, also add `<Route path="/p/:id/files" element={<FilesPage />} />`.

- [ ] **Step 4: Files tile** — `Tiles.tsx` takes `id` and renders the Files tile as a link:

```tsx
import { Link } from "react-router";
import { cx } from "@eggie/ui";
import { filesRoute } from "../../uploads/paths";
import { FOLDER, GLOBE, MAGNIFIER, TRASH } from "../icons";
import s from "./ProjectPage.module.css";

export function Tiles({ id, onAnalyze, onDelete }: { id: string; onAnalyze: () => void; onDelete: () => void }) {
  return (
    <div className={s.tiles}>
      <button type="button" className={s.tile} onClick={onAnalyze}>{MAGNIFIER}Analyze</button>
      <Link to={filesRoute(id, "")} className={s.tile}>{FOLDER}Files</Link>
      <button type="button" className={cx(s.tile, s.off)} disabled>{GLOBE}Public address<small>Needs an account</small></button>
      <button type="button" className={cx(s.tile, s.danger)} onClick={onDelete}>{TRASH}Delete</button>
    </div>
  );
}
```

In `ProjectPage.tsx` pass `id={project.id}` to `<Tiles …>`. Add `text-decoration: none;` to `.tile` in `ProjectPage.module.css` if the link renders underlined.

- [ ] **Step 5: `screens/files/Listing.tsx`**

```tsx
import { Link } from "react-router";
import { relativeTime, size } from "../../projects/format";
import type { DirEntry } from "../../uploads/queries";
import { fileUrl, filesRoute, joinPath } from "../../uploads/paths";
import { FOLDER } from "../icons";
import s from "./FilesPage.module.css";

export function Listing({ projectId, dir, entries, now }: { projectId: string; dir: string; entries: DirEntry[]; now: number }) {
  return (
    <ul className={s.rows}>
      <li className={s.headRow} aria-hidden="true">
        <span>Name</span>
        <span>Size</span>
        <span>Changed</span>
      </li>
      {entries.map((entry) => {
        const path = joinPath(dir, entry.name);
        const detail = entry.kind === "folder" ? (entry.items === null ? "—" : `${entry.items} ${entry.items === 1 ? "item" : "items"}`) : size(entry.size ?? 0);
        return (
          <li key={entry.name} className={s.row}>
            {entry.kind === "folder" ? (
              <Link to={filesRoute(projectId, path)} className={s.entry}>{FOLDER}{entry.name}</Link>
            ) : (
              <a href={fileUrl(projectId, path)} download={entry.name} className={s.entry}>{entry.name}</a>
            )}
            <span className={s.meta}>{detail}</span>
            <span className={s.meta}>{relativeTime(entry.modified, now)}</span>
          </li>
        );
      })}
    </ul>
  );
}
```

(`relativeTime` gives "2 hours ago"; the board's "Yesterday" is not required.)

- [ ] **Step 6: `screens/files/FilesPage.tsx`** (browse-only in this task; Tasks 7–8 add uploading)

```tsx
import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Notice } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { useNow } from "../../projects/useNow";
import { filesRoute, joinPath } from "../../uploads/paths";
import { useListing } from "../../uploads/queries";
import { useUploads } from "../../uploads/QueueProvider";
import { Listing } from "./Listing";
import s from "./FilesPage.module.css";

export function FilesPage() {
  const params = useParams();
  const id = params.id ?? "";
  const dir = joinPath(params["*"] ?? "");
  const listing = useListing(id, dir);
  const { queue } = useUploads(id);
  const navigate = useNavigate();
  const now = useNow();

  useEffect(() => {
    void queue.syncPending(id);
  }, [queue, id]);

  const code = listing.error instanceof ApiError ? listing.error.code : null;
  useEffect(() => {
    if (code === "folder_not_found" && dir !== "") navigate(filesRoute(id, ""), { replace: true });
  }, [code, dir, id, navigate]);

  if (code === "project_not_found") {
    return (
      <section className={s.page}>
        <Link to="/" className={s.back}>‹ All projects</Link>
        <h1 className={s.title}>No project called {id}</h1>
      </section>
    );
  }

  const segments = dir === "" ? [] : dir.split("/");
  const entries = listing.data?.entries ?? [];

  return (
    <section className={s.page}>
      <Link to={`/p/${encodeURIComponent(id)}`} className={s.back}>‹ {id}</Link>
      <header className={s.head}>
        <div className={s.grow}>
          <h1 className={s.title}>Files</h1>
          <nav className={s.crumbs} aria-label="Folder">
            <Link to={filesRoute(id, "")}>{id}</Link>
            {segments.map((segment, index) => (
              <span key={index}>
                {" / "}
                <Link to={filesRoute(id, segments.slice(0, index + 1).join("/"))}>{segment}</Link>
              </span>
            ))}
          </nav>
        </div>
      </header>
      {code === "permission_denied" ? (
        <Notice>Eggie can't look inside this folder — a program in the project owns it.</Notice>
      ) : listing.isError ? (
        <Notice>{listing.error.message}</Notice>
      ) : listing.data === undefined ? (
        <p className={s.muted}>Looking in the cupboard…</p>
      ) : (
        <>
          {entries.length > 0 && <Listing projectId={id} dir={dir} entries={entries} now={now} />}
          <p className={s.foot}>
            {entries.length > 0 && <strong>{entries.length} {entries.length === 1 ? "thing" : "things"} in here</strong>}{" "}
            Drag files in from your desktop, or use Upload to choose where they land.
          </p>
        </>
      )}
    </section>
  );
}
```

Read `projects/useNow.ts` first; if its signature differs (e.g. takes an interval), adapt the call.

- [ ] **Step 7: `screens/files/FilesPage.module.css`** — base styles (Tasks 7–8 append theirs):

```css
.page { display: flex; flex-direction: column; gap: 18px; max-width: 880px; width: 100%; margin: 0 auto; }
.back { align-self: flex-start; font: 600 14px var(--font-body); color: var(--ink-2); }
.head { display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap; }
.grow { flex: 1; min-width: 0; }
.title { margin: 0; font: 800 32px var(--font-display); letter-spacing: -.025em; color: var(--ink); }
.crumbs { margin-top: 4px; font: 400 14px var(--font-mono); color: var(--ink-2); overflow-wrap: anywhere; }
.crumbs a { color: inherit; }
.muted { margin: 0; font-size: 15px; color: var(--ink-3); }
.foot { margin: 0; font-size: 14px; color: var(--ink-3); }
.foot strong { color: var(--ink-2); font-weight: 600; }

.rows { margin: 0; padding: 0; list-style: none; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); }
.headRow, .row { display: grid; grid-template-columns: 1fr 110px 130px; gap: 12px; align-items: center; padding: 11px 16px; }
.headRow { font: 700 12px var(--font-body); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
.row { border-top: 1px solid var(--line); }
.entry { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 15px; color: var(--ink); overflow-wrap: anywhere; }
.meta { font-size: 14px; color: var(--ink-3); }

@media (max-width: 560px) {
  .headRow { display: none; }
  .row { grid-template-columns: 1fr auto; }
  .row > :last-child { display: none; }
}
```

- [ ] **Step 8: Verify**

Run: `npm run typecheck && npm test && npm run build && npm run check-offline`, then `npm run dev` and walk: project page → Files tile → `data/` → breadcrumb back; click a file (downloads); `?scenario=locked` → `data/` shows the permission notice; a made-up folder URL (`/p/recipe-box/files/nope`) lands on the top.
Expected: all clean; walk behaves as described.

- [ ] **Step 9: Commit**

```bash
git add apps/console/src/uploads/QueueProvider.tsx apps/console/src/uploads/queries.ts apps/console/src/screens/files apps/console/src/App.tsx apps/console/src/screens/project/Tiles.tsx apps/console/src/screens/project/ProjectPage.tsx apps/console/src/screens/project/ProjectPage.module.css
git commit -m "Add the Files screen: browse a project's folders and download files"
```

---

### Task 7: Getting files in — picker, destination dialog, won't fit, drop

**Files:**
- Create: `apps/console/src/screens/files/DestinationModal.tsx`
- Modify: `apps/console/src/screens/files/FilesPage.tsx`, `apps/console/src/screens/files/FilesPage.module.css`

**Interfaces:**
- Consumes: `useUploads` (Task 6), `useListing`, `useDisk` (Task 6), `fits` (Task 3), `joinPath`, `folderNameError` (Task 2), `size` from `projects/format.ts`, kit `Modal`, `Button`, `TextField`, `Notice`.
- Produces: `DestinationModal({ projectId, files, startDir, chooser, open, onClose, onSend, onPickAgain })` where `files: File[]`, `chooser: boolean` (false for dropped files: the won't-fit body only), `onSend(dir: string, files: File[]): void`, `onPickAgain(): void`.

- [ ] **Step 1: `DestinationModal.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Button, Modal, Notice, TextField } from "@eggie/ui";
import { size } from "../../projects/format";
import { folderNameError, joinPath } from "../../uploads/paths";
import { useDisk, useListing } from "../../uploads/queries";
import { fits } from "../../uploads/queue";
import s from "./FilesPage.module.css";

export function DestinationModal({
  projectId,
  files,
  startDir,
  chooser,
  open,
  onClose,
  onSend,
  onPickAgain,
}: {
  projectId: string;
  files: File[];
  startDir: string;
  chooser: boolean;
  open: boolean;
  onClose: () => void;
  onSend: (dir: string, files: File[]) => void;
  onPickAgain: () => void;
}) {
  const top = useListing(projectId, "");
  const disk = useDisk(open);
  const [choice, setChoice] = useState(startDir);
  const [newFolder, setNewFolder] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setChoice(startDir);
      setNewFolder(null);
    }
  }, [open, startDir]);

  const folders = (top.data?.entries ?? []).filter((e) => e.kind === "folder").map((e) => e.name);
  const choices = ["", ...folders, ...(startDir !== "" && !folders.includes(startDir) ? [startDir] : [])];
  const nameError = newFolder === null ? null : folderNameError(newFolder);
  const target = newFolder === null ? choice : joinPath(choice, newFolder.trim());
  const free = disk.data?.free_bytes;
  const tooBig = free === undefined ? [] : files.filter((f) => !fits(f.size, free));
  const fitting = files.filter((f) => !tooBig.includes(f));
  const total = files.reduce((sum, f) => sum + f.size, 0);
  const preview = files.length === 1 ? `${projectId}/${joinPath(target, files[0].name)}` : `${projectId}/${target === "" ? "" : `${target}/`}`;

  return (
    <Modal open={open} onClose={onClose} title={chooser ? "Where should this land?" : "This one won't fit"}>
      {chooser && <p className={s.modalSub}>Pick the folder it belongs in — no rummaging around later.</p>}
      <p className={s.picked}>
        <strong>{files.length === 1 ? files[0].name : `${files.length} files`}</strong> <span>{size(total)}</span>
      </p>
      {tooBig.length > 0 && free !== undefined && (
        <Notice>
          {tooBig.length === 1 && files.length === 1 ? "This one won't fit — the" : `${tooBig.map((f) => f.name).join(", ")} won't fit — the`}{" "}
          {tooBig.length === 1 ? `file is ${size(tooBig[0].size)}` : `files need ${size(tooBig.reduce((n, f) => n + f.size, 0))}`} and Eggie has{" "}
          {size(free)} of room left. Make some space in the desktop app, then come back and send it up — we'll still be here.
        </Notice>
      )}
      {chooser && (
        <>
          <ul className={s.choices}>
            {choices.map((dir) => (
              <li key={dir || "(top)"}>
                <label className={s.choice}>
                  <input type="radio" name="destination" checked={choice === dir} onChange={() => setChoice(dir)} />
                  {dir === "" ? <span><strong>{projectId}</strong> — the top of the project</span> : <span>{dir}</span>}
                </label>
              </li>
            ))}
          </ul>
          {newFolder === null ? (
            <Button variant="quiet" onClick={() => setNewFolder("")}>New folder</Button>
          ) : (
            <TextField label="New folder" value={newFolder} onChange={setNewFolder} autoFocus error={newFolder === "" ? undefined : (nameError ?? undefined)} />
          )}
          <p className={s.path}>{preview}</p>
        </>
      )}
      <div className={s.modalFoot}>
        {chooser && (
          <Button
            variant="primary"
            disabled={free === undefined || fitting.length === 0 || nameError !== null}
            onClick={() => {
              onSend(target, fitting);
              onClose();
            }}
          >
            Send it up
          </Button>
        )}
        {tooBig.length > 0 && <Button onClick={onPickAgain}>Pick a smaller file</Button>}
        <Button variant="quiet" onClick={onClose}>Cancel</Button>
      </div>
      {chooser && <p className={s.note}>Big files are fine — you can pause and come back.</p>}
      {disk.isError && <Notice>{disk.error.message}</Notice>}
    </Modal>
  );
}
```

- [ ] **Step 2: Wire into `FilesPage.tsx`** — add state, the hidden input, the Upload button, the drop zone and the dropped-folder notice:

```tsx
// new imports
import { useRef, useState, type DragEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@eggie/ui";
import { api } from "../../api/client";
import { fits } from "../../uploads/queue";
import type { Disk } from "../../uploads/uploadApi";
import { DestinationModal } from "./DestinationModal";

// inside FilesPage, after the existing hooks
  const client = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const [dialog, setDialog] = useState<{ files: File[]; chooser: boolean } | null>(null);
  const [folderRefused, setFolderRefused] = useState(false);
  const [dragging, setDragging] = useState(false);

  async function dropInto(files: File[]) {
    const disk = await client.fetchQuery({ queryKey: ["disk"], queryFn: () => api.get<Disk>("/api/disk"), staleTime: 0 });
    const fitting = files.filter((f) => fits(f.size, disk.free_bytes));
    if (fitting.length > 0) queue.add(id, dir, fitting);
    const tooBig = files.filter((f) => !fitting.includes(f));
    if (tooBig.length > 0) setDialog({ files: tooBig, chooser: false });
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const files: File[] = [];
    let folder = false;
    for (const item of Array.from(event.dataTransfer.items)) {
      if (item.kind !== "file") continue;
      // A folder shows up as a File too; only the entry API tells them apart.
      if (item.webkitGetAsEntry()?.isDirectory) {
        folder = true;
        continue;
      }
      const f = item.getAsFile();
      if (f) files.push(f);
    }
    setFolderRefused(folder);
    if (files.length > 0) void dropInto(files).catch(() => setDialog({ files, chooser: true }));
  }
```

In the header, after the title block:

```tsx
        <Button variant="primary" onClick={() => picker.current?.click()}>Upload</Button>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            if (files.length > 0) setDialog({ files, chooser: true });
          }}
        />
```

Wrap the listing/foot area in a drop zone:

```tsx
      <div
        className={cx(s.drop, dragging && s.dragging)}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        {/* the existing listing / notices / foot */}
      </div>
      {folderRefused && <Notice>Folders can't go up as they are — zip it first, then drop the zip.</Notice>}
      <DestinationModal
        projectId={id}
        files={dialog?.files ?? []}
        startDir={dir}
        chooser={dialog?.chooser ?? true}
        open={dialog !== null}
        onClose={() => setDialog(null)}
        onSend={(target, files) => queue.add(id, target, files)}
        onPickAgain={() => {
          setDialog(null);
          picker.current?.click();
        }}
      />
```

(Import `cx` from `@eggie/ui`.)

- [ ] **Step 3: Styles** — append to `FilesPage.module.css`:

```css
.drop { display: flex; flex-direction: column; gap: 12px; border-radius: 18px; outline: 2px dashed transparent; outline-offset: 6px; }
.dragging { outline-color: var(--yolk-deep); }
.modalSub { margin: 0; font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.modalFoot { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.note { margin: 0; font-size: 14px; color: var(--ink-3); }
.picked { margin: 0; display: flex; gap: 10px; align-items: baseline; font-size: 15px; overflow-wrap: anywhere; }
.picked span { color: var(--ink-3); font-size: 14px; }
.choices { margin: 0; padding: 0; list-style: none; border: 1px solid var(--line); border-radius: 14px; }
.choices li + li { border-top: 1px solid var(--line); }
.choice { display: flex; align-items: center; gap: 10px; padding: 10px 14px; font-size: 15px; cursor: pointer; }
.path { margin: 0; font: 400 13px var(--font-mono); color: var(--ink-2); overflow-wrap: anywhere; }
```

- [ ] **Step 4: Verify**

Run: `npm run typecheck && npm test && npm run build && npm run check-offline`, then `npm run dev`:
- Upload → pick one file → dialog preselects the folder you're in; New folder with `a/b` shows the error; Send it up queues it (the panel arrives in Task 8 — for now confirm with the listing refreshing when it lands).
- Drop a file on the listing → it goes into the current folder without a dialog; drop a folder → the zip notice.
- `?scenario=full` → picking anything over ~1.1 GB shows the won't-fit notice with "2.1 GB of room left" and Send disabled when nothing fits. (Use a sparse file: `truncate -s 3G /tmp/big.bin`.)
Expected: as described.

- [ ] **Step 5: Commit**

```bash
git add apps/console/src/screens/files
git commit -m "Let files be picked or dropped into a project, with a destination and a space check"
```

---

### Task 8: Carrying things in, out of room, done prompt, reload recovery

**Files:**
- Create: `apps/console/src/uploads/copy.ts`, `apps/console/src/screens/files/UploadPanel.tsx`, `apps/console/src/screens/files/DoneCard.tsx`
- Modify: `apps/console/src/screens/files/FilesPage.tsx`, `apps/console/src/screens/files/FilesPage.module.css`

**Interfaces:**
- Consumes: `UploadItem`, `UploadQueue` methods (Tasks 3–4), `useUploads` (Task 6), `secondsLeft`, `timeLeftWords` (Task 2), `kindOf`, `promptFor` (Task 2), `filesRoute`, `joinPath` (Task 2), `size` from `projects/format.ts`, kit `Button`, `ProgressBar`, `PromptCard`, `RowCard`, `Notice`, `Collapsible`.
- Produces:
  - `copy.ts`: `summary(items: readonly UploadItem[]): string`, `into(dir: string): string` (`"into data/"` / `"into the top of the project"`), `failure(item: UploadItem): string`.
  - `UploadPanel({ items, queue })`, `DoneCard({ item, onDismiss })`.

- [ ] **Step 1: `uploads/copy.ts`**

```ts
import type { UploadItem } from "./queue";

const PHRASE: Record<string, string> = {
  going: "going up",
  waiting: "waiting",
  paused: "paused",
  stalled: "stalled",
  noRoom: "stopped",
  failed: "didn't make it",
  done: "in",
};

export function summary(items: readonly UploadItem[]): string {
  const counts = new Map<string, number>();
  for (const item of items) counts.set(item.state, (counts.get(item.state) ?? 0) + 1);
  return [...counts].map(([state, n]) => `${n} ${PHRASE[state]}`).join(" · ");
}

export function into(dir: string): string {
  return dir === "" ? "into the top of the project" : `into ${dir}/`;
}

export function failure(item: UploadItem): string {
  switch (item.reason) {
    case "file_exists":
      return `${item.name} is already in ${item.dir === "" ? "the top of the project" : `${item.dir}/`}. Replace it?`;
    case "permission_denied":
      return "Eggie can't write into that folder — pick another.";
    case "project_not_found":
      return "The project is gone.";
    case "path_is_folder":
      return "There's a folder with that name already.";
    case "upload_not_found":
      return "Eggie lost this upload — send it again.";
    default:
      return item.message ?? "Something went wrong.";
  }
}
```

- [ ] **Step 2: `screens/files/UploadPanel.tsx`**

```tsx
import { useRef, useState } from "react";
import { Link } from "react-router";
import { Button, Collapsible, Notice, ProgressBar, RowCard } from "@eggie/ui";
import { size } from "../../projects/format";
import { failure, into, summary } from "../../uploads/copy";
import { secondsLeft, timeLeftWords } from "../../uploads/eta";
import { filesRoute } from "../../uploads/paths";
import type { UploadItem, UploadQueue } from "../../uploads/queue";
import s from "./FilesPage.module.css";

const percent = (item: UploadItem) => (item.size === 0 ? 100 : Math.floor((item.offset / item.size) * 100));

export function UploadPanel({ items, queue }: { items: readonly UploadItem[]; queue: UploadQueue }) {
  const [stillFull, setStillFull] = useState<number | null>(null);
  if (items.length === 0) return null;
  const full = items.find((item) => item.state === "noRoom" && item.uploadId !== null);

  async function carryOn() {
    const moved = await queue.carryOn().catch(() => false);
    // Read the queue, not `items`: this closure's copy predates carryOn's update.
    setStillFull(moved ? null : (queue.snapshot().find((i) => i.state === "noRoom")?.freeBytes ?? 0));
  }

  return (
    <>
      {full && (
        <RowCard accent="trouble" className={s.banner}>
          <h2 className={s.bannerTitle}>Eggie ran out of room partway through</h2>
          <p className={s.bannerText}>
            {full.name} got {percent(full)}% of the way in. What made it is safe, and it can carry on from there — but you'll need to
            free up space in the desktop app first.
          </p>
          <Button variant="primary" onClick={carryOn}>I've freed some up — carry on</Button>
          {stillFull !== null && <Notice>Still not enough room — {size(stillFull)} free.</Notice>}
        </RowCard>
      )}
      <Collapsible boxed defaultOpen summary="Carrying things in" aside={summary(items)}>
        <ul className={s.queue}>
          {items.map((item) => (
            <UploadRow key={item.key} item={item} queue={queue} onCarryOn={carryOn} />
          ))}
        </ul>
        <p className={s.note}>Pausing is fine — nothing is lost. If you close this page, uploads pick up where they stopped when you're back.</p>
      </Collapsible>
    </>
  );
}

function UploadRow({ item, queue, onCarryOn }: { item: UploadItem; queue: UploadQueue; onCarryOn: () => void }) {
  const picker = useRef<HTMLInputElement>(null);
  const [wrongFile, setWrongFile] = useState(false);
  const got = `${size(item.offset)} of ${size(item.size)}`;
  const left = item.state === "going" ? secondsLeft(item.samples, item.size - item.offset) : null;

  let line: string;
  let actions: React.ReactNode = null;
  switch (item.state) {
    case "going":
      line = item.busy ? "Waiting for the project to finish starting" : `${got} · ${into(item.dir)}${left === null ? "" : ` · ${timeLeftWords(left)}`}`;
      actions = !item.busy && <Button size="md" onClick={() => queue.pause(item.key)}>Pause</Button>;
      break;
    case "waiting":
      line = `${size(item.size)} · waiting its turn`;
      actions = <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>;
      break;
    case "paused":
      line = `Paused at ${percent(item)}% · ${into(item.dir)}`;
      actions = (
        <>
          <Button onClick={() => queue.resume(item.key)}>Carry on</Button>
          <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>
        </>
      );
      break;
    case "stalled":
      line = `The ${item.fromReload ? "page was closed" : "connection dropped"} at ${percent(item)}%. Nothing was lost — it can carry on from there.`;
      actions = (
        <>
          <Button onClick={() => (item.file ? queue.resume(item.key) : picker.current?.click())}>Pick up where it stopped</Button>
          <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>
          <input
            ref={picker}
            type="file"
            hidden
            onChange={(event) => {
              const picked = event.target.files?.[0];
              event.target.value = "";
              if (picked) setWrongFile(!queue.relink(item.key, picked));
            }}
          />
        </>
      );
      break;
    case "noRoom":
      line = item.uploadId !== null ? `Stopped at ${percent(item)}% · ${got} got through · ${into(item.dir)}` : `Won't fit — ${size(item.size)}, with ${size(item.freeBytes ?? 0)} of room left`;
      actions = <Button onClick={onCarryOn}>Carry on</Button>;
      break;
    case "failed":
      line = failure(item);
      actions =
        item.reason === "file_exists" ? (
          <>
            <Button onClick={() => queue.replace(item.key)}>Replace</Button>
            <Button variant="quiet" onClick={() => queue.remove(item.key)}>Skip</Button>
          </>
        ) : (
          <Button variant="quiet" onClick={() => queue.remove(item.key)}>Remove</Button>
        );
      break;
    case "done":
      line = `In ${item.dir === "" ? "the top of the project" : `${item.dir}/`} · ${size(item.size)}`;
      actions = <Link to={filesRoute(item.projectId, item.dir)} className={s.showMe}>Show me</Link>;
      break;
  }

  return (
    <li className={s.upload}>
      <div className={s.uploadHead}>
        <strong className={s.uploadName}>{item.name}</strong>
        {(item.state === "going" || item.state === "paused") && <span className={s.meta}>{percent(item)}%</span>}
        <span className={s.uploadActions}>{actions}</span>
      </div>
      {item.state === "going" && <ProgressBar value={item.offset / Math.max(1, item.size)} label={`${item.name} upload`} />}
      <p className={item.state === "failed" || item.state === "noRoom" ? s.uploadTrouble : s.uploadLine}>{line}</p>
      {wrongFile && (
        <Notice>
          That's not the same file — pick {item.name} ({size(item.size)}).
        </Notice>
      )}
    </li>
  );
}
```

(Import `type ReactNode` from `react` and use it instead of `React.ReactNode`.)

- [ ] **Step 3: `screens/files/DoneCard.tsx`**

```tsx
import { Button, PromptCard } from "@eggie/ui";
import { size } from "../../projects/format";
import { kindOf, promptFor } from "../../uploads/kinds";
import { joinPath } from "../../uploads/paths";
import type { UploadItem } from "../../uploads/queue";
import s from "./FilesPage.module.css";

export function DoneCard({ item, onDismiss }: { item: UploadItem; onDismiss: () => void }) {
  const kind = kindOf(item.name);
  if (!kind) return null;
  return (
    <section className={s.done}>
      <h2 className={s.doneTitle}>
        <code>{item.name}</code> is in — it landed in <code>{item.dir === "" ? "the top of the project" : `${item.dir}/`}</code>. All {size(item.size)} of it.
      </h2>
      <p className={s.doneLead}>
        <strong>One more step, and it's not yours to figure out.</strong>{" "}
        {kind === "dump"
          ? "A file sitting in a folder isn't a database yet. Hand this to your coding agent and it'll do the rest."
          : "An archive sitting in a folder isn't unpacked yet. Hand this to your coding agent and it'll do the rest."}
      </p>
      <PromptCard prompt={promptFor(kind, joinPath(item.dir, item.name))} aside="for Claude Code, Codex, whoever's cooking" />
      <div className={s.modalFoot}>
        <Button onClick={onDismiss}>Back to files</Button>
        <span className={s.note}>Or ignore all this — the file is in the folder either way.</span>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Wire into `FilesPage.tsx`** — take `items`, `landed`, `dismiss` from `useUploads(id)`, and render above the drop zone:

```tsx
      {landed && <DoneCard item={landed} onDismiss={dismiss} />}
      <UploadPanel items={items} queue={queue} />
```

- [ ] **Step 5: Styles** — append to `FilesPage.module.css`:

```css
.banner { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; }
.bannerTitle { margin: 0; font: 700 17px var(--font-body); color: var(--ink); }
.bannerText { margin: 0; font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.queue { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 14px; }
.upload { display: flex; flex-direction: column; gap: 6px; }
.uploadHead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.uploadName { flex: 1; min-width: 0; font-size: 15px; overflow-wrap: anywhere; }
.uploadActions { display: flex; gap: 8px; flex-wrap: wrap; }
.uploadLine { margin: 0; font-size: 14px; color: var(--ink-3); }
.uploadTrouble { margin: 0; font-size: 14px; color: var(--paprika); }
.showMe { font: 600 14px var(--font-body); color: var(--ink-2); }
.done { display: flex; flex-direction: column; gap: 12px; }
.doneTitle { margin: 0; font: 700 20px/1.3 var(--font-body); color: var(--ink); overflow-wrap: anywhere; }
.doneTitle code { font: 600 17px var(--font-mono); }
.doneLead { margin: 0; font-size: 15.5px; line-height: 1.5; color: var(--ink-2); }
```

- [ ] **Step 6: Verify**

Run: `npm run typecheck && npm test && npm run build && npm run check-offline`, then `npm run dev` and walk each scenario at 880 px and 1200 px, light and dark:
- `ok`: upload a ~50 MB file (`truncate -s 50M /tmp/f.bin`): progress, time left appears after ~5 s, Pause/Carry on, a second file waits and can be Removed; navigate to the project page and back — still going. Upload `x.sql` → done card with the dump prompt; `x.zip` → archive prompt; `x.txt` → no card. Upload a file that already exists (`README.md` into the top) → Replace/Skip.
- `busy`: the last chunk shows "Waiting for the project to finish starting", then lands.
- `fills-up`: a >20 MB file stops at the third chunk with the frame-14 banner; "I've freed some up — carry on" resumes it.
- `uploads`: Files for recipe-box shows two stalled rows "The page was closed at 38%"/"71%"; picking any file shows "That's not the same file"; Remove clears a row.
- With an upload going, reloading the tab asks for confirmation.
Expected: as described.

- [ ] **Step 7: Commit**

```bash
git add apps/console/src/uploads/copy.ts apps/console/src/screens/files
git commit -m "Show uploads in flight, running out of room, and the prompt when a file lands"
```

---

### Task 9: Docs and final verification (controller)

**Files:**
- Modify: `CLAUDE.md` (repo root) — **the user has uncommitted edits in this file; commit only this task's hunks.**

- [ ] **Step 1: Edit the two lines** in `CLAUDE.md`:
  - The dev line `npm run dev          # Vite + an in-browser mock agent; ?scenario=…` gains `|uploads|full|fills-up|busy|locked`.
  - The `web/` bullet, after the `projects/slugify.ts` sentence, gains: "`uploads/queue.ts` owns the chunked-upload protocol (resume at the agent's offset, busy retry on the last chunk, hold on `disk_full`) with no React in it; one instance lives above the router in `uploads/QueueProvider.tsx`, so uploads carry on across screens but stop when the page closes."

- [ ] **Step 2: Stage only those hunks** without touching the user's edit: build the committed version from `HEAD`:

```bash
cd /home/ihor/projects/local-environment-for-non-tech/poc
git show HEAD:CLAUDE.md > "$SCRATCH/CLAUDE.md"
# apply the same two edits to "$SCRATCH/CLAUDE.md" (Edit tool), then:
blob=$(git hash-object -w "$SCRATCH/CLAUDE.md")
git update-index --cacheinfo 100644,"$blob",CLAUDE.md
git diff --cached CLAUDE.md   # must show only the two new edits
git diff CLAUDE.md            # must still show the user's own edit (and nothing of ours)
git commit -m "Note part D's upload queue and mock scenarios in CLAUDE.md"
```

- [ ] **Step 3: Final verification**

Run from `web/`: `npm test && npm run typecheck && npm run build && npm run check-offline`; from the repo root: `TMPDIR=<writable dir> python3 -m pytest -q` (no Python changed; confirms nothing else moved).
Expected: all green.
