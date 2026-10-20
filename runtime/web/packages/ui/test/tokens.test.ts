import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const TOKENS = join(WEB, "packages/ui/src/tokens.css");

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    if (name === "node_modules" || name === "dist") return [];
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return cssFiles(path);
    return name.endsWith(".css") ? [path] : [];
  });
}

const declared = (css: string) => new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));

const files = [...cssFiles(join(WEB, "packages/ui/src")), ...cssFiles(join(WEB, "apps/console/src"))];

describe("design tokens", () => {
  it("has CSS files to check", () => {
    expect(files.length).toBeGreaterThan(10);
  });

  // A token renamed by a redesign leaves var(--old) resolving to nothing, silently.
  it("every var() names a token from tokens.css or the same file", () => {
    const shared = declared(readFileSync(TOKENS, "utf8"));
    const offences: string[] = [];
    for (const file of files) {
      const css = readFileSync(file, "utf8");
      const own = declared(css);
      for (const m of css.matchAll(/var\(\s*(--[\w-]+)/g)) {
        if (!shared.has(m[1]) && !own.has(m[1])) offences.push(`${relative(WEB, file)}: ${m[1]}`);
      }
    }
    expect([...new Set(offences)]).toEqual([]);
  });
});
