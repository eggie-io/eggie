import { describe, expect, it } from "vitest";
import { planImport, type Picked } from "./plan";

const pick = (path: string, size = 1): Picked => ({ path, file: new File([new Uint8Array(size)], path.split("/").pop()!) });

describe("planImport", () => {
  it("names the project after the folder and strips the folder from each path", async () => {
    const plan = await planImport([pick("My App/src/main.py", 3), pick("My App/README.md", 2)]);
    expect(plan.name).toBe("My App");
    expect(plan.id).toBe("my-app");
    expect(plan.entries.map((e) => e.path)).toEqual(["src/main.py", "README.md"]);
    expect(plan.bytes).toBe(5);
  });

  it("accepts a dropped folder's leading slash", async () => {
    const plan = await planImport([pick("/shop/app.py")]);
    expect(plan.name).toBe("shop");
    expect(plan.entries.map((e) => e.path)).toEqual(["app.py"]);
  });

  it("leaves out dependency trees, repositories and caches wherever they sit, and says so", async () => {
    const plan = await planImport([
      pick("app/src/index.ts", 10),
      pick("app/node_modules/react/index.js", 500),
      pick("app/.git/HEAD", 20),
      pick("app/web/node_modules/x.js", 30),
      pick("app/.venv/bin/python", 900),
      pick("app/lib/__pycache__/a.pyc", 7),
      pick("app/.eggie/overlay.yml", 4),
      pick("app/.eggie/project.yml", 6),
    ]);
    expect(plan.entries.map((e) => e.path)).toEqual(["src/index.ts", ".eggie/project.yml"]);
    expect(plan.bytes).toBe(16);
    expect(plan.skipped).toEqual([".git", ".venv", "__pycache__", "node_modules"]);
  });

  it("reports nothing skipped when nothing was", async () => {
    expect((await planImport([pick("a/b.txt")])).skipped).toEqual([]);
  });

  it("refuses a folder whose name makes no address", async () => {
    const plan = await planImport([pick("---/a.txt")]);
    expect(plan.id).toBe("");
  });

  it("hands the page back between batches so a big folder doesn't freeze it", async () => {
    const files = Array.from({ length: 1200 }, (_, i) => pick(`big/f${i}.txt`));
    let pauses = 0;
    const plan = await planImport(files, async () => void (pauses += 1));
    expect(pauses).toBe(2);
    expect(plan.entries).toHaveLength(1200);
  });

  it("an empty folder is a plan with nothing in it, not a crash", async () => {
    const plan = await planImport([]);
    expect(plan).toEqual({ name: "", id: "", entries: [], bytes: 0, skipped: [] });
  });
});
