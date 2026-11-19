import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { type Account, createAccountLink, liveLink, signInLink, signInView, startError } from "./account";

describe("signInLink", () => {
  it("accepts an https sign-in page", () => {
    const url = "https://app.eggie.io/device?user_code=ABCD-EFGH";
    expect(signInLink(url)).toBe(url);
  });

  it("refuses anything that is not https, local dev addresses included", () => {
    expect(signInLink("http://localhost:5174/device?user_code=X")).toBeNull();
    expect(signInLink("javascript:alert(1)")).toBeNull();
    expect(signInLink("not a url")).toBeNull();
  });
});

describe("startError", () => {
  it("routes a lost console session to sign-out, not a retry", () => {
    const error = new ApiError("session_expired", "expired", 401);
    expect(startError(error)).toEqual({ kind: "sessionLost", reason: "session_expired" });
  });

  it("treats an unreachable service as a retryable state", () => {
    const error = new ApiError("cloud_unavailable", "down", 503);
    const outcome = startError(error);
    expect(outcome.kind).toBe("unreachable");
  });

  it("surfaces the service's own message for any other failure", () => {
    const error = new ApiError("cloud_error", "the service would not start a sign-in", 502);
    expect(startError(error)).toEqual({
      kind: "error",
      message: "Sign-in didn't start: the service would not start a sign-in",
    });
  });

  it("falls back to a generic message for a non-API error", () => {
    expect(startError(new Error("boom"))).toEqual({ kind: "error", message: "Sign-in didn't start." });
  });
});

const LINK = "https://app.eggie.io/device?user_code=ABCD-EFGH";
const pending = (over: Partial<Extract<Account, { state: "pending" }>> = {}): Account => ({
  state: "pending", user_code: "ABCD-EFGH", url: LINK, expires_at: 2000, ...over,
});

describe("createAccountLink", () => {
  it("sends a new person to sign-up with the approval page as the way back", () => {
    expect(createAccountLink(LINK)).toBe("https://app.eggie.io/signup?redirect=%2Fdevice%3Fuser_code%3DABCD-EFGH");
  });
});

describe("liveLink", () => {
  it("gives the link of a code that can still be approved", () => {
    expect(liveLink(pending(), 1_999_000)).toBe(LINK);
  });

  it("gives nothing once the code has run out", () => {
    expect(liveLink(pending(), 2_000_000)).toBeNull();
  });

  it("gives nothing without a pending code or for a link that is not https", () => {
    expect(liveLink(null, 0)).toBeNull();
    expect(liveLink({ state: "signed_out", error: null }, 0)).toBeNull();
    expect(liveLink(pending({ url: "http://localhost:5174/device?user_code=X" }), 0)).toBeNull();
  });
});

describe("signInView", () => {
  it("keeps the choice showing while a code waits that nobody pressed a button for", () => {
    expect(signInView(pending(), null)).toEqual({ kind: "choose", reason: null });
  });

  it("opens sign-up for a new person and the approval page for one with an account", () => {
    expect(signInView(pending(), "new")).toEqual({
      kind: "finish", intent: "new", code: "ABCD-EFGH", host: "app.eggie.io", expiresAt: 2000,
      target: "https://app.eggie.io/signup?redirect=%2Fdevice%3Fuser_code%3DABCD-EFGH",
    });
    expect(signInView(pending(), "existing")).toMatchObject({ kind: "finish", target: LINK });
  });

  it("builds no address from a link that is not https", () => {
    const url = "http://localhost:5174/device?user_code=X";
    expect(signInView(pending({ url }), "new")).toEqual({ kind: "badLink", url });
  });

  it("says why a sign-in was refused, but not that an unasked-for code ran out", () => {
    expect(signInView({ state: "signed_out", error: "access_denied" }, null)).toEqual({
      kind: "choose", reason: "The sign-in was turned down.",
    });
    expect(signInView({ state: "signed_out", error: "expired_token" }, null)).toEqual({ kind: "choose", reason: null });
  });
});
