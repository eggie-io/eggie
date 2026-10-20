import { describe, expect, it } from "vitest";
import { nameProblem, splitSecrets } from "./secrets";

describe("nameProblem", () => {
  it("accepts an ordinary env name", () => {
    expect(nameProblem("OPENAI_API_KEY", [])).toBeNull();
  });
  it("asks for a name when empty", () => {
    expect(nameProblem("  ", [])).toMatch(/name/i);
  });
  it.each(["1KEY", "MY-KEY", "A B"])("refuses %s like the API does", (name) => {
    expect(nameProblem(name, [])).toMatch(/letters, digits/);
  });
  it.each(["COMPOSE_FILE", "docker_host"])("refuses the reserved %s", (name) => {
    expect(nameProblem(name, [])).toMatch(/reserved/);
  });
  it.each(["ld_preload", "PATH", "home", "BUILDX_BUILDER", "buildkit_host"])("refuses the reserved %s", (name) => {
    expect(nameProblem(name, [])).toMatch(/reserved/);
  });
  it("allows a name that only starts like PATH", () => {
    expect(nameProblem("PATHS", [])).toBeNull();
  });
  it("points at Replace for a name that already exists", () => {
    expect(nameProblem("API_KEY", ["API_KEY"])).toMatch(/Replace/);
  });
});

describe("splitSecrets", () => {
  const entry = (name: string, unused: boolean) => ({ name, updated_at: 1, unused });
  it("separates unused names and keeps each group in API order", () => {
    const { used, unused } = splitSecrets({
      secrets: [entry("B", false), entry("OLD_A", true), entry("A", false), entry("OLD_B", true)],
      requested: [],
      restart_needed: false,
    });
    expect(used.map((x) => x.name)).toEqual(["B", "A"]);
    expect(unused.map((x) => x.name)).toEqual(["OLD_A", "OLD_B"]);
  });
});
