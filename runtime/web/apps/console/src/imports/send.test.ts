import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { BUSY_DEADLINE_MS, runImport, type ImportApi, type Progress } from "./send";

function setup(failures: Partial<Record<keyof ImportApi, Error[]>> = {}) {
  const calls: string[] = [];
  const progress: Progress[] = [];
  const sleeps: number[] = [];
  let clock = 0;
  const fail = (method: keyof ImportApi) => {
    const next = failures[method]?.shift();
    if (next) throw next;
  };
  const api: ImportApi = {
    async create(id) {
      calls.push(`create ${id}`);
      fail("create");
    },
    async remove(id) {
      calls.push(`remove ${id}`);
      fail("remove");
    },
    async send(id, archive, onProgress) {
      calls.push(`send ${id} ${archive.size}`);
      fail("send");
      onProgress(archive.size, archive.size);
    },
  };
  const run = (mode: "merge" | "replace") =>
    runImport(api, {
      id: "shop",
      mode,
      archive: new Blob(["tar.gz"]),
      onProgress: (p) => progress.push(p),
      sleep: async (ms) => {
        sleeps.push(ms);
        clock += ms;
      },
      now: () => clock,
    });
  return { calls, progress, sleeps, run };
}

const busy = () => new ApiError("project_busy", "busy", 409);

describe("runImport", () => {
  it("merge: creates, sends, and reports how much went", async () => {
    const { calls, progress, run } = setup();
    await run("merge");
    expect(calls).toEqual(["create shop", "send shop 6"]);
    expect(progress).toEqual([
      { sent: 0, total: 6 },
      { sent: 6, total: 6 },
    ]);
  });

  it("merge tolerates a project that already exists", async () => {
    const { calls, run } = setup({ create: [new ApiError("project_exists", "exists", 409)] });
    await run("merge");
    expect(calls).toEqual(["create shop", "send shop 6"]);
  });

  it("replace deletes before it creates", async () => {
    const { calls, run } = setup();
    await run("replace");
    expect(calls).toEqual(["remove shop", "create shop", "send shop 6"]);
  });

  it("replace of a project that vanished in the meantime still goes through", async () => {
    const { calls, run } = setup({ remove: [new ApiError("project_not_found", "gone", 404)] });
    await run("replace");
    expect(calls).toEqual(["remove shop", "create shop", "send shop 6"]);
  });

  it("replace does not swallow project_exists: the delete was supposed to clear it", async () => {
    const { run } = setup({ create: [new ApiError("project_exists", "exists", 409)] });
    await expect(run("replace")).rejects.toMatchObject({ code: "project_exists" });
  });

  it("retries a busy project until it takes the archive", async () => {
    const { calls, sleeps, run } = setup({ send: [busy(), busy()] });
    await run("merge");
    expect(calls).toEqual(["create shop", "send shop 6", "send shop 6", "send shop 6"]);
    expect(sleeps).toHaveLength(2);
  });

  it("gives up on a project that stays busy past the deadline", async () => {
    const { run } = setup({ send: Array.from({ length: BUSY_DEADLINE_MS }, busy) });
    await expect(run("merge")).rejects.toMatchObject({ code: "project_busy" });
  });

  it("any other failure surfaces as it is", async () => {
    const { calls, run } = setup({ send: [new ApiError("payload_too_large", "too big", 413)] });
    await expect(run("merge")).rejects.toMatchObject({ code: "payload_too_large" });
    expect(calls).toEqual(["create shop", "send shop 6"]);
  });
});
