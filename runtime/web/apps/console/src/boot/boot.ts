import type { Account } from "../account/account";
import { type Api, ApiError, createApi, isSessionLost } from "../api/client";
import { SUPPORTED_API } from "../api/version";

export type SignedOutReason = "not_signed_in" | "session_expired" | "handoff_spent";

// site is set once /api/health has answered: the host name on a site, null on
// the usual VM. Results that never reached the health answer leave it unset.
// webApp is the Eggie web app address from the same answer, same rules.
export type BootResult = { site?: string | null; webApp?: string | null } & (
  | { kind: "signedIn"; landing: string | null }
  | { kind: "needsAccount" }
  | { kind: "signedOut"; reason: SignedOutReason }
  | { kind: "needsUpdate"; apiVersion: number | null }
  | { kind: "notAnswering" }
  | { kind: "wrongHost" }
);

function isWrongHost(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 403 &&
    (error.code === "forbidden_host" || error.code === "forbidden_origin")
  );
}

// Only a same-origin path may become the address bar: not "//host", not "/\\host".
export function landingPath(value: unknown): string | null {
  if (typeof value !== "string" || !value.startsWith("/")) return null;
  return value[1] === "/" || value[1] === "\\" ? null : value;
}

async function checkAccount(api: Api, landing: string | null): Promise<BootResult> {
  try {
    const account = await api.get<Account>("/api/account");
    return account?.state === "signed_in" ? { kind: "signedIn", landing } : { kind: "needsAccount" };
  } catch (error) {
    if (isSessionLost(error)) return { kind: "signedOut", reason: error.code };
    return isWrongHost(error) ? { kind: "wrongHost" } : { kind: "notAnswering" };
  }
}

export function takeHandoff(
  location: Pick<Location, "hash" | "pathname" | "search">,
  history: Pick<History, "replaceState">,
): string | null {
  const code = new URLSearchParams(location.hash.replace(/^#/, "")).get("handoff");
  if (!code) return null;
  history.replaceState(null, "", location.pathname + location.search);
  return code;
}

export async function boot({
  fetch,
  handoff,
}: {
  fetch: typeof globalThis.fetch;
  handoff: string | null;
}): Promise<BootResult> {
  const api = createApi(fetch);

  let health: { api?: unknown; site?: unknown; web_app?: unknown } | undefined;
  try {
    health = await api.get<{ api?: unknown; site?: unknown; web_app?: unknown }>("/api/health");
  } catch (error) {
    return isWrongHost(error) ? { kind: "wrongHost" } : { kind: "notAnswering" };
  }
  const site = typeof health?.site === "string" && health.site !== "" ? health.site : null;
  const webApp = typeof health?.web_app === "string" && health.web_app !== "" ? health.web_app : null;
  const result = await signIn(api, health?.api, handoff);
  // After a presented code, a lost session means the code did not take (e.g.
  // cookies blocked); the site must not redirect to the web app again.
  const signedOut = handoff !== null && result.kind === "signedOut";
  return { ...(signedOut ? { ...result, reason: "handoff_spent" as const } : result), site, webApp };
}

async function signIn(api: Api, healthApi: unknown, handoff: string | null): Promise<BootResult> {
  const apiVersion = typeof healthApi === "number" ? healthApi : null;
  if (apiVersion === null || !SUPPORTED_API.includes(apiVersion)) {
    return { kind: "needsUpdate", apiVersion };
  }

  let spent = false;
  if (handoff !== null) {
    try {
      const started = await api.post<{ return_path?: unknown }>("/api/session", { code: handoff });
      return checkAccount(api, landingPath(started?.return_path));
    } catch (error) {
      if (isWrongHost(error)) return { kind: "wrongHost" };
      if (!(error instanceof ApiError && error.code === "handoff_invalid")) {
        return { kind: "notAnswering" };
      }
      // An older cookie may still be good; only its absence makes this an error.
      spent = true;
    }
  }

  try {
    await api.get("/api/session");
    return checkAccount(api, null);
  } catch (error) {
    if (isSessionLost(error)) {
      return { kind: "signedOut", reason: spent ? "handoff_spent" : error.code };
    }
    return isWrongHost(error) ? { kind: "wrongHost" } : { kind: "notAnswering" };
  }
}
