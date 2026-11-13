export type CloseReason = "gone" | "refused" | "unavailable" | "ended" | "dropped";

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

// `opened` separates a handshake the server refused (auth, origin) from a live socket that died:
// both close with 1006 and no reason.
export function closeReason(code: number, opened: boolean): CloseReason {
  if (code === 4404 || code === 4409) return "gone";
  if (code === 4401 || code === 4403 || code === 1008) return "refused";
  if (code === 4503) return "unavailable";
  if (code === 1000) return "ended";
  if (code === 1006 && !opened) return "refused";
  return "dropped";
}

// 1 s, 2 s, then every 5 s.
export const reconnectDelay = (attempt: number): number => [1000, 2000][attempt] ?? 5000;
