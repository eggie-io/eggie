import { createContext, useContext } from "react";

// null is the usual VM, a string a site. undefined only on a non-local address
// until /api/health answers; a local address starts as null.
export const SiteContext = createContext<string | null | undefined>(undefined);

export const useSite = () => useContext(SiteContext);

// The Eggie web app address; null until known or when the server reports none.
export const WebAppContext = createContext<string | null | undefined>(undefined);

export const useWebApp = () => useContext(WebAppContext);

export const LOCAL_HOSTS = ["localhost", "127.0.0.1"];

export function isLocalHost(hostname: string): boolean {
  return LOCAL_HOSTS.includes(hostname);
}

// A result that never read /api/health (a lost session) keeps the last value.
export function nextSite(
  prev: string | null | undefined,
  result: { site?: string | null },
): string | null | undefined {
  return result.site !== undefined ? result.site : prev;
}

export function nextWebApp(
  prev: string | null | undefined,
  result: { webApp?: string | null },
): string | null | undefined {
  return result.webApp !== undefined ? result.webApp : prev;
}
