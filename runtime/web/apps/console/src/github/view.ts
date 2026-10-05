import type { GitHubStatus } from "./github";

export type GitHubView =
  | { kind: "connect"; problem: string | null; action: string }
  | { kind: "code"; code: string; expiresAt: number }
  | { kind: "applying"; login: string }
  | { kind: "ready"; login: string }
  | { kind: "setupFailed"; login: string; message: string; canRetry: boolean }
  | { kind: "reconnect"; login: string };

const PROBLEMS: Record<string, { problem: string; action: string }> = {
  access_denied: { problem: "You said no on GitHub, so nothing was connected.", action: "Try again" },
  expired_token: { problem: "That code ran out before it was entered on GitHub.", action: "Get a new code" },
};
const UNEXPECTED = { problem: "GitHub answered with something unexpected.", action: "Try again" };

export function githubView(status: GitHubStatus): GitHubView {
  switch (status.state) {
    case "disconnected":
      if (!status.error) return { kind: "connect", problem: null, action: "Connect GitHub" };
      return { kind: "connect", ...(PROBLEMS[status.error] ?? UNEXPECTED) };
    case "pending":
      return { kind: "code", code: status.user_code, expiresAt: status.expires_at };
    case "needs_reconnect":
      return { kind: "reconnect", login: status.login };
    case "connected":
      if (status.setup === "ready") return { kind: "ready", login: status.login };
      if (status.setup === "applying") return { kind: "applying", login: status.login };
      if (status.setup === "runtime_outdated") {
        return {
          kind: "setupFailed", login: status.login, canRetry: false,
          message: "This machine's Eggie runtime needs an update before GitHub can be set up. " +
            "Open the desktop app and choose Repair.",
        };
      }
      return {
        kind: "setupFailed", login: status.login, canRetry: true,
        message: status.setup_error === "setup_timeout"
          ? "GitHub setup inside Eggie didn't finish."
          : `GitHub setup inside Eggie failed: ${status.setup_error ?? "no reason given"}`,
      };
  }
}
