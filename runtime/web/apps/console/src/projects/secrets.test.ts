import { describe, expect, it } from "vitest";
import type { SecretsView } from "./types";
import { fieldState, fileToBase64, nameProblem, splitSecrets } from "./secrets";

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
  const entry = (name: string, unused: boolean) => ({ name, kind: "text" as const, filename: null, updated_at: 1, unused });
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

describe("fieldState", () => {
  const view = (kind: "text" | "file"): SecretsView => ({
    secrets: [{ name: "ASC_API_KEY", kind, filename: kind === "file" ? "AuthKey.p8" : null, updated_at: 5, unused: false }],
    requested: [],
    restart_needed: false,
  });
  it("needs the same kind", () => {
    expect(fieldState(view("text"), "ASC_API_KEY", true).saved).toBe(false);
    expect(fieldState(view("file"), "ASC_API_KEY", false).saved).toBe(false);
  });
  it("reports the stored file", () => {
    expect(fieldState(view("file"), "ASC_API_KEY", true)).toEqual({ saved: true, filename: "AuthKey.p8", updatedAt: 5 });
  });
  it("is unsaved before the list loads", () => {
    expect(fieldState(undefined, "ASC_API_KEY", false).saved).toBe(false);
  });
});

describe("fileToBase64", () => {
  it("encodes bytes, including NUL", async () => {
    expect(await fileToBase64(new File([new Uint8Array([0, 255, 65])], "k.bin"))).toBe("AP9B");
  });
  it("refuses a file over 64 KB before upload", async () => {
    await expect(fileToBase64(new File([new Uint8Array(64 * 1024 + 1)], "big"))).rejects.toThrow("A key file can be at most 64 KB.");
  });
});
