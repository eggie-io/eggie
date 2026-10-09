import { ApiError } from "../api/client";

export type Mode = "merge" | "replace";

export interface Progress {
  sent: number;
  total: number;
}

export interface ImportApi {
  create(id: string): Promise<unknown>;
  remove(id: string): Promise<unknown>;
  send(id: string, archive: Blob, onProgress: (sent: number, total: number) => void): Promise<unknown>;
}

export interface ImportRun {
  id: string;
  mode: Mode;
  archive: Blob;
  onProgress: (progress: Progress) => void;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

export const BUSY_RETRY_MS = 1000;
export const BUSY_DEADLINE_MS = 60_000;

const isCode = (error: unknown, code: string) => error instanceof ApiError && error.code === code;

export async function runImport(api: ImportApi, run: ImportRun): Promise<void> {
  const sleep = run.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const now = run.now ?? (() => Date.now());

  // `project_busy` means another lifecycle operation holds the project's
  // lock: retry, like the host client, rather than fail.
  async function whileBusy<T>(call: () => Promise<T>): Promise<T> {
    const deadline = now() + BUSY_DEADLINE_MS;
    for (;;) {
      try {
        return await call();
      } catch (error) {
        if (!isCode(error, "project_busy") || now() >= deadline) throw error;
      }
      await sleep(BUSY_RETRY_MS);
    }
  }

  if (run.mode === "replace") {
    // Delete first. The other order merges the folder in and then wipes it.
    try {
      await whileBusy(() => api.remove(run.id));
    } catch (error) {
      if (!isCode(error, "project_not_found")) throw error;
    }
  }
  try {
    await api.create(run.id);
  } catch (error) {
    if (!(run.mode === "merge" && isCode(error, "project_exists"))) throw error;
  }

  run.onProgress({ sent: 0, total: run.archive.size });
  await whileBusy(() => api.send(run.id, run.archive, (sent, total) => run.onProgress({ sent, total })));
}
