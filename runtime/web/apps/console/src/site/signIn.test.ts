import { describe, expect, it } from "vitest";
import { instancePage, openInstanceUrl, webAppInstances, siteSignedOutAction } from "./signIn";

describe("openInstanceUrl", () => {
  it("builds the web app page link with the host", () =>
    expect(openInstanceUrl("https://app.eggie.io/", "foo.eggie.io")).toBe(
      "https://app.eggie.io/kitchens/open?host=foo.eggie.io",
    ));
  it("accepts a plain-http localhost web app for development", () =>
    expect(openInstanceUrl("http://localhost:5173", "foo.eggie.io")).toBe(
      "http://localhost:5173/kitchens/open?host=foo.eggie.io",
    ));
  it.each(["http://app.eggie.io", "javascript:alert(1)", "", "not a url"])("refuses web app %s", (webApp) =>
    expect(openInstanceUrl(webApp, "foo.eggie.io")).toBeNull());
});

describe("siteSignedOutAction", () => {
  it.each(["not_signed_in", "session_expired"] as const)("redirects at once for %s", (r) =>
    expect(siteSignedOutAction(r)).toBe("redirect"));
  it("offers a button after a refused code, so a failing redeem cannot loop", () =>
    expect(siteSignedOutAction("handoff_spent")).toBe("button"));
});

describe("webAppInstances", () => {
  it("links the instances list, and nothing for an unusable web app", () => {
    expect(webAppInstances("https://app.eggie.io/")).toBe("https://app.eggie.io/kitchens");
    expect(webAppInstances("http://app.eggie.io")).toBeNull();
    expect(webAppInstances(null)).toBeNull();
  });
});

describe("instancePage", () => {
  it("links this instance's page in the web app", () =>
    expect(instancePage("https://app.eggie.io/", "01a1-77")).toBe("https://app.eggie.io/kitchens/01a1-77"));
  it("falls back to the instances list while the id is unknown", () =>
    expect(instancePage("https://app.eggie.io", null)).toBe("https://app.eggie.io/kitchens"));
  it("keeps an odd id inside its path segment", () =>
    expect(instancePage("https://app.eggie.io", "a/../b")).toBe("https://app.eggie.io/kitchens/a%2F..%2Fb"));
  it("links nothing for an unusable web app", () => expect(instancePage("http://app.eggie.io", "x")).toBeNull());
});
