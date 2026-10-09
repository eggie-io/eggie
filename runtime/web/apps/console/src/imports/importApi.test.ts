import { describe, expect, it } from "vitest";
import type { Api } from "../api/client";
import { createImportApi } from "./importApi";

// A plain DELETE keeps the project's folder and volumes, which would turn a
// "replace" into a merge under a warning that says everything is gone.
describe("createImportApi", () => {
  it("removes a project with purge, so a replace really empties it", async () => {
    const calls: string[] = [];
    const client = { del: async (path: string) => void calls.push(`DELETE ${path}`), post: async () => undefined } as unknown as Api;
    await createImportApi(client, async () => undefined).remove("shop");
    expect(calls).toEqual(["DELETE /api/projects/shop?purge=true"]);
  });
});
