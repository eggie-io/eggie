import { describe, expect, it } from "vitest";
import { fetchSite } from "./site";

function answering(status: number, body: unknown): typeof fetch {
  return (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
}

describe("fetchSite", () => {
  it("is the hostname the API reports on a site", async () => {
    expect(await fetchSite(answering(200, { api: 1, site: "foo.eggie.io" }))).toBe("foo.eggie.io");
  });

  it("is null locally, on an older API without the field, and on an empty name", async () => {
    expect(await fetchSite(answering(200, { api: 1, site: null }))).toBeNull();
    expect(await fetchSite(answering(200, { api: 1 }))).toBeNull();
    expect(await fetchSite(answering(200, { api: 1, site: "" }))).toBeNull();
  });

  it("is null when the API does not answer or refuses the host", async () => {
    expect(await fetchSite(answering(403, { error: { code: "forbidden_host" } }))).toBeNull();
    const refused = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;
    expect(await fetchSite(refused)).toBeNull();
  });
});
