import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseAgent, parseIndex } from "./catalog";

// The manifests live outside the web workspace; the Dockerfile copies them to
// the same relative place (/runtime/agents) so this path holds in the image too.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../../../../agents");
const read = (path: string) => JSON.parse(readFileSync(join(ROOT, path), "utf8"));

describe("shipped agent content", () => {
  const ids = parseIndex(read("index.json"));

  it.each(ids)("%s parses, has both platforms, and every file it names exists", (id) => {
    const agent = parseAgent(id, read(`${id}/agent.json`), "");
    expect(agent).not.toBeNull();
    expect(Object.keys(agent!.platforms).sort()).toEqual(["mac", "windows"]);
    const files = [agent!.icon, ...Object.values(agent!.platforms).flatMap((g) => g!.steps.flatMap((s) => s.media.map((m) => m.src)))];
    for (const file of files) if (file) expect(existsSync(join(ROOT, file)), file).toBe(true);
  });
});

describe("serving the content", () => {
  const WEB = join(dirname(fileURLToPath(import.meta.url)), "../../../..");

  it("no nginx location without the SPA fallback swallows a console route", () => {
    const nginx = readFileSync(join(WEB, "nginx.conf"), "utf8");
    const app = readFileSync(join(WEB, "apps/console/src/App.tsx"), "utf8");
    const strict = [...nginx.matchAll(/location\s+(\/\S*)\s*\{[^}]*try_files \$uri =404;/g)].map((m) => m[1]);
    const routes = [...app.matchAll(/<Route path="([^"]+)"/g)].map((m) => m[1].replace(/:[^/]+/g, "x"));
    expect(strict.length).toBeGreaterThan(0);
    expect(routes.length).toBeGreaterThan(0);
    const swallowed = routes.filter((route) => strict.some((prefix) => route.startsWith(prefix)));
    expect(swallowed).toEqual([]);
  });
});
