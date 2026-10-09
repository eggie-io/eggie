import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const TOKENS = join(WEB, "packages/ui/src/tokens.css");

function modules(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    if (name === "node_modules" || name === "dist") return [];
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return modules(path);
    return name.endsWith(".module.css") ? [path] : [];
  });
}

// CSS Modules renames every animation name in a module file to a local one,
// and the shared keyframes in tokens.css have no local twin, so a bare
// `animation: om-spin` silently never runs. Every kit animation once broke this way.
describe("shared animations", () => {
  const shared = [...readFileSync(TOKENS, "utf8").matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
  const files = [...modules(join(WEB, "packages")), ...modules(join(WEB, "apps"))];

  it("has keyframes and module files to check", () => {
    expect(shared.length).toBeGreaterThan(3);
    expect(files.length).toBeGreaterThan(5);
  });

  it("are referenced through global() from module files", () => {
    const offences: string[] = [];
    for (const file of files) {
      for (const [declaration] of readFileSync(file, "utf8").matchAll(/animation(?:-name)?\s*:[^;}]*/g)) {
        for (const name of shared) {
          if (new RegExp(`(^|[\\s:,])${name}\\b`).test(declaration)) offences.push(`${relative(WEB, file)}: ${declaration.trim()}`);
        }
      }
    }
    expect(offences).toEqual([]);
  });
});
