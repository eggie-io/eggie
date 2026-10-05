import { describe, expect, it } from "vitest";
import { boot, takeHandoff } from "./boot";

type Reply = { status: number; body?: unknown; raw?: string } | "refused";

function api(routes: Record<string, Reply>) {
  const calls: string[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    calls.push(key);
    const reply = routes[key];
    if (reply === undefined || reply === "refused") throw new TypeError("Failed to fetch");
    const text = reply.raw ?? (reply.body === undefined ? null : JSON.stringify(reply.body));
    return new Response(text, { status: reply.status });
  }) as typeof fetch;
  return { fetch: fetchImpl, calls };
}

const HEALTHY: Reply = { status: 200, body: { status: "ok", api: 1 } };
const OK: Reply = { status: 200, body: { signed_in: true } };
const ACCOUNT = (state: string): Reply => ({ status: 200, body: { state } });
const refusal = (status: number, code: string): Reply => ({
  status,
  body: { error: { code, message: code } },
});

describe("boot", () => {
  it("is notAnswering when the API refuses the connection", async () => {
    const { fetch } = api({ "GET /api/health": "refused" });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "notAnswering" });
  });

  it("is notAnswering when something other than the API answers /api/health", async () => {
    const { fetch } = api({ "GET /api/health": { status: 200, raw: "<!doctype html>" } });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "notAnswering" });
  });

  it("stops at needsUpdate for an api number the page doesn't speak, before any session call", async () => {
    const { fetch, calls } = api({ "GET /api/health": { status: 200, body: { api: 2 } } });
    expect(await boot({ fetch, handoff: "code" })).toEqual({ kind: "needsUpdate", apiVersion: 2 });
    expect(calls).toEqual(["GET /api/health"]);
  });

  it("treats a health answer without an api number as needing an update", async () => {
    const { fetch } = api({ "GET /api/health": { status: 200, body: { status: "ok" } } });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "needsUpdate", apiVersion: null });
  });

  it("signs in with a fresh handoff code without asking for the session again", async () => {
    const { fetch, calls } = api({
      "GET /api/health": HEALTHY,
      "POST /api/session": OK,
      "GET /api/account": ACCOUNT("signed_in"),
    });
    expect(await boot({ fetch, handoff: "code" })).toEqual({ kind: "signedIn" });
    expect(calls).toEqual(["GET /api/health", "POST /api/session", "GET /api/account"]);
  });

  it("is still signed in when the handoff was spent but the cookie is good", async () => {
    const { fetch } = api({
      "GET /api/health": HEALTHY,
      "POST /api/session": refusal(401, "handoff_invalid"),
      "GET /api/session": OK,
      "GET /api/account": ACCOUNT("signed_in"),
    });
    expect(await boot({ fetch, handoff: "old" })).toEqual({ kind: "signedIn" });
  });

  it("says the link was spent when the handoff fails and there is no session", async () => {
    const { fetch } = api({
      "GET /api/health": HEALTHY,
      "POST /api/session": refusal(401, "handoff_invalid"),
      "GET /api/session": refusal(401, "not_signed_in"),
    });
    expect(await boot({ fetch, handoff: "old" })).toEqual({ kind: "signedOut", reason: "handoff_spent" });
  });

  it("passes on why the session is gone", async () => {
    for (const code of ["not_signed_in", "session_expired"] as const) {
      const { fetch } = api({ "GET /api/health": HEALTHY, "GET /api/session": refusal(401, code) });
      expect(await boot({ fetch, handoff: null })).toEqual({ kind: "signedOut", reason: code });
    }
  });

  it("is notAnswering for any other refusal of the session check", async () => {
    const { fetch } = api({ "GET /api/health": HEALTHY, "GET /api/session": refusal(500, "internal_error") });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "notAnswering" });
  });

  it("is wrongHost when the API refuses the page's address", async () => {
    const { fetch } = api({ "GET /api/health": refusal(403, "forbidden_host") });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "wrongHost" });
  });

  it("is wrongHost when the API refuses the page's origin on sign-in", async () => {
    const { fetch } = api({ "GET /api/health": HEALTHY, "POST /api/session": refusal(403, "forbidden_origin") });
    expect(await boot({ fetch, handoff: "code" })).toEqual({ kind: "wrongHost" });
  });

  it("is wrongHost when the session check is refused for the address", async () => {
    const { fetch } = api({ "GET /api/health": HEALTHY, "GET /api/session": refusal(403, "forbidden_host") });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "wrongHost" });
  });

  it("is notAnswering when the handoff fails for a reason other than a spent code", async () => {
    const { fetch } = api({ "GET /api/health": HEALTHY, "POST /api/session": refusal(500, "internal") });
    expect(await boot({ fetch, handoff: "code" })).toEqual({ kind: "notAnswering" });
  });

  it("needs an account until the Eggie sign-in has finished", async () => {
    for (const state of ["signed_out", "pending"]) {
      const { fetch } = api({ "GET /api/health": HEALTHY, "GET /api/session": OK, "GET /api/account": ACCOUNT(state) });
      expect(await boot({ fetch, handoff: null })).toEqual({ kind: "needsAccount" });
    }
  });

  it("is notAnswering when the account check fails for another reason", async () => {
    const { fetch } = api({
      "GET /api/health": HEALTHY,
      "GET /api/session": OK,
      "GET /api/account": refusal(500, "internal_error"),
    });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "notAnswering" });
  });
});

describe("takeHandoff", () => {
  it("returns the code and removes the hash, keeping path and query", () => {
    const replaced: string[] = [];
    const code = takeHandoff(
      { hash: "#handoff=abc123", pathname: "/kit", search: "?scenario=ok" },
      { replaceState: (_data, _unused, url) => void replaced.push(String(url)) },
    );
    expect(code).toBe("abc123");
    expect(replaced).toEqual(["/kit?scenario=ok"]);
  });

  it("leaves history alone when there is no code", () => {
    const replaced: string[] = [];
    const code = takeHandoff(
      { hash: "", pathname: "/", search: "" },
      { replaceState: (_data, _unused, url) => void replaced.push(String(url)) },
    );
    expect(code).toBeNull();
    expect(replaced).toEqual([]);
  });
});
