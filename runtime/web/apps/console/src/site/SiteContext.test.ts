import { describe, expect, it } from "vitest";
import { nextSite } from "./SiteContext";

describe("nextSite", () => {
  it("keeps the last site when the result never read the health answer", () => {
    expect(nextSite("foo.eggie.io", {})).toBe("foo.eggie.io");
  });

  it("lets a null answer replace both unknown and a previous site", () => {
    expect(nextSite(undefined, { site: null })).toBeNull();
    expect(nextSite("foo.eggie.io", { site: null })).toBeNull();
  });
});
