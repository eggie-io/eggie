import { describe, expect, it } from "vitest";
import { openKitchenUrl, webAppKitchens, siteSignedOutAction } from "./signIn";

describe("openKitchenUrl", () => {
  it("builds the web app page link with the host", () =>
    expect(openKitchenUrl("https://app.eggie.io/", "foo.eggie.io")).toBe(
      "https://app.eggie.io/kitchens/open?host=foo.eggie.io",
    ));
  it("accepts a plain-http localhost web app for development", () =>
    expect(openKitchenUrl("http://localhost:5173", "foo.eggie.io")).toBe(
      "http://localhost:5173/kitchens/open?host=foo.eggie.io",
    ));
  it.each(["http://app.eggie.io", "javascript:alert(1)", "", "not a url"])("refuses web app %s", (webApp) =>
    expect(openKitchenUrl(webApp, "foo.eggie.io")).toBeNull());
});

describe("siteSignedOutAction", () => {
  it.each(["not_signed_in", "session_expired"] as const)("redirects at once for %s", (r) =>
    expect(siteSignedOutAction(r)).toBe("redirect"));
  it("offers a button after a refused code, so a failing redeem cannot loop", () =>
    expect(siteSignedOutAction("handoff_spent")).toBe("button"));
});

describe("webAppKitchens", () => {
  it("links the kitchens list, and nothing for an unusable web app", () => {
    expect(webAppKitchens("https://app.eggie.io/")).toBe("https://app.eggie.io/kitchens");
    expect(webAppKitchens("http://app.eggie.io")).toBeNull();
    expect(webAppKitchens(null)).toBeNull();
  });
});
