import type { Api } from "../api/client";

type Store = Pick<Storage, "getItem" | "setItem">;

const KEY = "eggie.desktopHome";

// The desktop app serves its own screens from a random loopback port and
// passes that address in the handoff link. Anything else is refused: the
// link can be crafted, and Home must never lead off this machine.
function isDesktopHome(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" && parsed.hostname === "127.0.0.1"
      && parsed.port !== "" && parsed.username === "" && parsed.password === "";
  } catch {
    return false;
  }
}

export function desktopHome(store: Store | null): string | null {
  let stored: string | null = null;
  try {
    stored = store?.getItem(KEY) ?? null;
  } catch {
    // Storage can be blocked; the page then only knows what this load was given.
  }
  return stored !== null && isDesktopHome(stored) ? stored : null;
}

// Read before takeHandoff() clears the fragment.
export function takeDesktopHome(location: Pick<Location, "hash">, store: Store | null): string | null {
  const home = new URLSearchParams(location.hash.replace(/^#/, "")).get("home");
  if (home === null || !isDesktopHome(home)) return desktopHome(store);
  try {
    store?.setItem(KEY, home);
  } catch {
    // As above: a reload inside the window loses the Home button, nothing else.
  }
  return home;
}

const CONNECT_KEY = "eggie.desktopConnect";
const AGENT_ID = /^[a-z0-9][a-z0-9-]*$/;

function agentIds(value: string | null): string[] | null {
  if (value === null) return null;
  const ids = value.split(",").filter((id) => id !== "");
  return ids.every((id) => AGENT_ID.test(id)) ? ids : null;
}

// The agents the desktop window can connect by itself (`connect=` in the
// handoff link), remembered like Home. Empty in a plain browser.
// Read before takeHandoff() clears the fragment.
export function takeDesktopConnect(location: Pick<Location, "hash">, store: Store | null): string[] {
  const given = agentIds(new URLSearchParams(location.hash.replace(/^#/, "")).get("connect"));
  if (given !== null) {
    try {
      store?.setItem(CONNECT_KEY, given.join(","));
    } catch {
      // As for Home: a reload inside the window loses the button, nothing else.
    }
    return given;
  }
  let stored: string | null = null;
  try {
    stored = store?.getItem(CONNECT_KEY) ?? null;
  } catch {
    // Blocked storage: nothing remembered.
  }
  return agentIds(stored) ?? [];
}

// The desktop screen that connects `agent`, then reopens `back` here. The
// window checks both again and asks the user before it changes anything.
export function desktopConnectUrl(home: string, agent: string, back: string): string {
  const url = new URL(home);
  url.hash = new URLSearchParams({ connect: agent, back }).toString();
  return url.toString();
}

export async function browserLink(api: Pick<Api, "post">, origin: string): Promise<string> {
  try {
    const { code } = await api.post<{ code: string }>("/api/sessions/handoff");
    return `${origin}/#handoff=${encodeURIComponent(code)}`;
  } catch {
    return origin;
  }
}

// An anchor click, not window.open(): the desktop window hands only link
// activations to the system browser on macOS.
export function openExternal(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.click();
}
