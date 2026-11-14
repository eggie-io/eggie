export type CloseReason = "gone" | "refused" | "unavailable" | "failed" | "ended" | "dropped";

export function terminalUrl(
  location: { protocol: string; host: string },
  projectId: string,
  actionId: string,
  cols: number,
  rows: number,
): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const path = `/api/projects/${encodeURIComponent(projectId)}/actions/${encodeURIComponent(actionId)}/terminal`;
  return `${scheme}//${location.host}${path}?cols=${cols}&rows=${rows}`;
}

// `everOpened`: this panel has had a terminal attached. Before that, 1006 is a refused handshake
// (auth, origin) and 4503 a helper that is down; after it, both are a restart to reconnect through.
export function closeReason(code: number, everOpened: boolean): CloseReason {
  if (code === 4404 || code === 4409) return "gone";
  if (code === 4401 || code === 4403 || code === 1008) return "refused";
  if (code === 4503) return everOpened ? "dropped" : "unavailable";
  // The helper answered but could not attach: retrying would fail the same way.
  if (code === 4502) return "failed";
  if (code === 1000) return "ended";
  if (code === 1006 && !everOpened) return "refused";
  return "dropped";
}

// 1 s, 2 s, then every 5 s.
export const reconnectDelay = (attempt: number): number => [1000, 2000][attempt] ?? 5000;
