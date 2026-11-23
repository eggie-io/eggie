import type { Intent } from "../account/account";

export type EventContext = {
  sessionId: string;
  context: { app_version: string; os: "windows" | "macos"; os_version: string; arch: string };
};

type Body = {
  event: string;
  session_id: string;
  properties: { method?: Intent };
  context: EventContext["context"];
};

// Same shapes the API accepts: a crafted link must not make the console send
// what the API would refuse.
const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const APP_VERSION = /^[0-9A-Za-z.+-]{1,32}$/;
const OS_VERSION = /^[0-9A-Za-z._-]{1,32}$/;
const ARCH = /^[0-9A-Za-z_-]{1,16}$/;

// Read before takeHandoff() clears the fragment. Memory only, never stored.
export function takeDesktopEvents(location: Pick<Location, "hash">): EventContext | null {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const sid = params.get("sid");
  const app = params.get("app");
  const os = params.get("os");
  const osv = params.get("osv");
  const arch = params.get("arch");
  if (sid === null || app === null || os === null || osv === null || arch === null) return null;
  if (!SESSION_ID.test(sid) || !APP_VERSION.test(app) || !OS_VERSION.test(osv) || !ARCH.test(arch)) return null;
  if (os !== "windows" && os !== "macos") return null;
  return { sessionId: sid, context: { app_version: app, os, os_version: osv, arch } };
}

const defaultPost = (body: Body): Promise<unknown> =>
  fetch("/api/desktop-events", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

let current: EventContext | null = null;
let post: (body: Body) => Promise<unknown> = defaultPost;

export function setEventContext(ctx: EventContext | null): void {
  current = ctx;
}

// Test hook; null restores the real request.
export function setEventPoster(next: ((body: Body) => Promise<unknown>) | null): void {
  post = next ?? defaultPost;
}

export function reportEvent(event: "signup_screen_shown" | "signup_started", properties?: { method: Intent }): void {
  if (current === null) return;
  try {
    post({ event, session_id: current.sessionId, properties: properties ?? {}, context: current.context })
      .catch(() => {});
  } catch {
    // Telemetry never breaks the screen.
  }
}
