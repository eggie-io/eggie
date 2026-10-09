import { ApiError } from "../api/client";

export type Mode = "merge" | "replace";

export type Phase =
  | { kind: "packing"; read: number; total: number }
  | { kind: "sending"; sent: number; total: number };

export interface ImportApi {
  create(id: string): Promise<unknown>;
  remove(id: string): Promise<unknown>;
  send(id: string, archive: Blob, onProgress: (sent: number, total: number) => void): Promise<unknown>;
}

export interface ImportRun {
  id: string;
  mode: Mode;
  bytes: number;
  pack: (onRead: (bytes: number) => void) => Promise<Blob>;
  onPhase: (phase: Phase) => void;
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

  // Packed before anything is touched: a replace deletes a project, and a
  // folder that turns out unpackable must not cost one.
  let read = 0;
  run.onPhase({ kind: "packing", read, total: run.bytes });
  const archive = await run.pack((bytes) => {
    read += bytes;
    run.onPhase({ kind: "packing", read, total: run.bytes });
  });

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

  run.onPhase({ kind: "sending", sent: 0, total: archive.size });
  await whileBusy(() => api.send(run.id, archive, (sent, total) => run.onPhase({ kind: "sending", sent, total })));
}
