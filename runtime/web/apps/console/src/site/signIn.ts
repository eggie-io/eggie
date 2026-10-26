import type { SignedOutReason } from "../boot/boot";

// Null when web_app is not an https address (http only for localhost).
function webAppOrigin(webApp: string | null | undefined): string | null {
  if (!webApp) return null;
  let base: URL;
  try {
    base = new URL(webApp);
  } catch {
    return null;
  }
  const local = base.protocol === "http:" && (base.hostname === "localhost" || base.hostname === "127.0.0.1");
  return base.protocol === "https:" || local ? base.origin : null;
}

// The web app page that signs the person in and sends them back with a handoff code.
export function openInstanceUrl(webApp: string, host: string): string | null {
  const origin = webAppOrigin(webApp);
  return origin === null ? null : `${origin}/kitchens/open?${new URLSearchParams({ host })}`;
}

// The web app's list of instances, for screens with nothing to retry.
export function webAppInstances(webApp: string | null | undefined): string | null {
  const origin = webAppOrigin(webApp);
  return origin === null ? null : `${origin}/kitchens`;
}

// A refused code means the web app already sent us back once; redirecting
// again would loop.
export function siteSignedOutAction(reason: SignedOutReason): "redirect" | "button" {
  return reason === "handoff_spent" ? "button" : "redirect";
}
