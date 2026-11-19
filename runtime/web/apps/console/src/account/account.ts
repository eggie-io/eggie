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

// The link of a waiting code, so a press can open it at once. The API drops a
// code that ran out; this computer's clock, which may differ from the VM's, is not asked.
export function liveLink(account: Account | null): string | null {
  return account?.state === "pending" ? signInLink(account.url) : null;
}

// The web app's sign-up page, which sends the new account on to the approval page.
export function createAccountLink(link: string): string {
  const { origin, pathname, search } = new URL(link);
  return `${origin}/signup?${new URLSearchParams({ redirect: pathname + search })}`;
}

// Which button the person pressed: it decides the page that opens and step 1.
export type Intent = "new" | "existing";

export type SignInView =
  | { kind: "choose"; reason: string | null }
  | { kind: "finish"; intent: Intent; code: string; target: string; host: string; expiresAt: number }
  | { kind: "badLink"; url: string };

export function signInView(account: Account | null, intent: Intent | null): SignInView {
  if (account?.state === "pending" && intent !== null) {
    const link = signInLink(account.url);
    if (link === null) return { kind: "badLink", url: account.url };
    return {
      kind: "finish", intent, code: account.user_code, expiresAt: account.expires_at,
      target: intent === "new" ? createAccountLink(link) : link, host: new URL(link).host,
    };
  }
  return { kind: "choose", reason: idleReason(account) };
}

// The screen asks for a code before anyone presses a button, so one that
// ran out unused is not news.
function idleReason(account: Account | null): string | null {
  return signInError(account?.state === "signed_out" && account.error !== "expired_token" ? account.error : null);
}

// What the screen keeps between answers; `notice` outlives the account that
// carried it, `opened` says whether the browser page was really opened.
export type Waiting = { account: Account | null; intent: Intent | null; notice: string | null; opened: boolean };

// A press can open the page only while its link is already here: a window
// opened after waiting for the code is refused by the desktop webview on macOS.
export function afterPress<T extends Waiting>(was: T, intent: Intent): { next: T; open: string | null } {
  const view = signInView(was.account, intent);
  const open = view.kind === "finish" ? view.target : null;
  return { next: { ...was, intent: open === null ? was.intent : intent, opened: open !== null, notice: null }, open };
}

export function afterPoll<T extends Waiting>(was: T, account: Account): T {
  if (account.state === "pending" || was.intent === null) return { ...was, account };
  // The code they were waiting on is gone: back to the choice, saying why.
  return { ...was, account, intent: null, notice: signInError(account.state === "signed_out" ? account.error : null) };
}

// A code asked for ahead of a press replaces the account whose error the
// screen was showing, and the API forgets that error with it.
export function afterQuietCode<T extends Waiting>(was: T, account: Account): T {
  return { ...was, account, notice: was.notice ?? idleReason(was.account) };
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
