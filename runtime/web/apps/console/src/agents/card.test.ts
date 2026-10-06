import { describe, expect, it } from "vitest";
import type { Guide } from "./catalog";
import { cardRows } from "./card";

const fourFields: Guide = {
  card: [
    { label: "Host", value: "{host}" },
    { label: "Port", value: "{port}" },
    { label: "User", value: "{user}" },
    { label: "Key file", value: "{key_file}" },
  ],
  tagline: "",
  warning: null,
  steps: [],
};
const userAtHost: Guide = { card: [{ label: "SSH Host", value: "{user}@{host}" }, { label: "SSH Port", value: "{port}" }], tagline: "", warning: null, steps: [] };
const wsl: Guide = { card: null, tagline: "", warning: null, steps: [] };
const lima = { vm: "lima" as const, ssh: { host: "127.0.0.1", port: 39022, user: "ada", key_file: "~/k" } };

describe("cardRows", () => {
  it("shows no card for a guide without one", () => {
    expect(cardRows(wsl, lima)).toBeNull();
  });

  it("fills each field the agent's form asks for, in the manifest's order", () => {
    expect(cardRows(fourFields, lima)).toEqual([
      { label: "Host", value: "127.0.0.1", copy: true },
      { label: "Port", value: "39022", copy: true },
      { label: "User", value: "ada", copy: true },
      { label: "Key file", value: "~/k", copy: true },
    ]);
  });

  it("combines facts in one field when the agent wants user@host", () => {
    expect(cardRows(userAtHost, lima)).toEqual([
      { label: "SSH Host", value: "ada@127.0.0.1", copy: true },
      { label: "SSH Port", value: "39022", copy: true },
    ]);
  });

  it("says the user is unknown rather than offering a broken copy", () => {
    const rows = cardRows(fourFields, { vm: "other", ssh: null });
    expect(rows?.find((row) => row.label === "User")).toEqual({ label: "User", value: "your Mac user name", copy: false });
    expect(rows?.find((row) => row.label === "Port")).toEqual({ label: "Port", value: "39022", copy: true });
    expect(cardRows(userAtHost, undefined)?.[0]).toEqual({ label: "SSH Host", value: "your Mac user name@127.0.0.1", copy: false });
  });
});
