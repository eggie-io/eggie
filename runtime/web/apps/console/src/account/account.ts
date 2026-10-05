import { ApiError, type SessionLoss, isSessionLost } from "../api/client";

export type Account =
  | { state: "signed_out"; error: string | null }
  | { state: "pending"; user_code: string; url: string; expires_at: number }
  | {
      state: "signed_in";
      email: string | null;
      sync: { last_ok_at: number | null; last_error: string | null };
    };

// The link comes from the Eggie service; anything but https is refused, never repaired.
export function signInLink(url: string): string | null {
  try {
    return new URL(url).protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

const REASONS: Record<string, string> = {
  access_denied: "The sign-in was turned down.",
  expired_token: "That code ran out before it was approved.",
  invalid_grant: "That code is no longer valid.",
  revoked: "This computer was signed out of your Eggie account.",
};

export function signInError(code: string | null): string | null {
  if (!code) return null;
  return REASONS[code] ?? "Sign-in didn't finish.";
}

export type StartOutcome =
  | { kind: "sessionLost"; reason: SessionLoss }
  | { kind: "unreachable"; message: string }
  | { kind: "error"; message: string };

// Maps a failed POST /api/account/sign-in to what the screen should do:
// a lost console session needs the sign-out screen, not a retry here.
export function startError(error: unknown): StartOutcome {
  if (isSessionLost(error)) return { kind: "sessionLost", reason: error.code };
  if (error instanceof ApiError && error.code === "cloud_unavailable") {
    return {
      kind: "unreachable",
      message: "The Eggie service can't be reached right now. Check the internet connection.",
    };
  }
  if (error instanceof ApiError) {
    return { kind: "error", message: `Sign-in didn't start: ${error.message}` };
  }
  return { kind: "error", message: "Sign-in didn't start." };
}
