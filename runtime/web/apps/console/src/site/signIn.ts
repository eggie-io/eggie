import type { SignedOutReason } from "../boot/boot";

// The web app page that signs the person in and sends them back with a handoff
// code. Null when web_app is not an https address (http only for localhost).
export function openKitchenUrl(webApp: string, host: string, home: string | null): string | null {
  let base: URL;
  try {
    base = new URL(webApp);
  } catch {
    return null;
  }
  const local = base.protocol === "http:" && (base.hostname === "localhost" || base.hostname === "127.0.0.1");
  if (base.protocol !== "https:" && !local) return null;
  const query = new URLSearchParams({ host });
  if (home !== null) query.set("home", home);
  return `${base.origin}/kitchens/open?${query}`;
}

// The web app's list of kitchens, for screens with nothing to retry.
export function webAppKitchens(webApp: string | null | undefined): string | null {
  const link = webApp ? openKitchenUrl(webApp, "x", null) : null;
  return link === null ? null : `${new URL(link).origin}/kitchens`;
}

// A refused code means the web app already sent us back once; redirecting
// again would loop.
export function siteSignedOutAction(reason: SignedOutReason): "redirect" | "button" {
  return reason === "handoff_spent" ? "button" : "redirect";
}
