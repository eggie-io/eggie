import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import {
  type Account, afterPoll, afterPress, afterQuietCode, createAccountLink, liveLink, signInLink, signInView, startError,
} from "./account";

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
  it("gives the link of a waiting code, whatever this computer's clock says", () => {
    expect(liveLink(pending({ expires_at: 1 }))).toBe(LINK);
  });

  it("gives nothing without a waiting code or for a link that is not https", () => {
    expect(liveLink(null)).toBeNull();
    expect(liveLink({ state: "signed_out", error: null })).toBeNull();
    expect(liveLink(pending({ url: "http://localhost:5174/device?user_code=X" }))).toBeNull();
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

describe("afterPoll", () => {
  const waiting = { account: pending(), intent: "new" as const, notice: null, opened: true };

  it("keeps waiting while the code is still there", () => {
    expect(afterPoll(waiting, pending())).toEqual(waiting);
  });

  it("goes back to the choice and says why when the code was turned down", () => {
    const account: Account = { state: "signed_out", error: "access_denied" };
    expect(afterPoll(waiting, account)).toMatchObject({ account, intent: null, notice: "The sign-in was turned down." });
  });

  it("tells a person who was waiting that the code ran out", () => {
    const next = afterPoll(waiting, { state: "signed_out", error: "expired_token" });
    expect(next.notice).toBe("That code ran out before it was approved.");
  });
});

describe("afterQuietCode", () => {
  it("keeps the reason on screen when a new code replaces the account that carried it", () => {
    const was = { account: { state: "signed_out", error: "revoked" } as Account, intent: null, notice: null, opened: false };
    const next = afterQuietCode(was, pending());
    expect(next.notice).toBe("This computer was signed out of your Eggie account.");
    expect(signInView(next.account, next.intent).kind).toBe("choose");
  });

  it("does not bring the waiting view back after a refusal", () => {
    const refused = afterPoll({ account: pending(), intent: "existing", notice: null, opened: true }, { state: "signed_out", error: "access_denied" });
    const next = afterQuietCode(refused, pending());
    expect(signInView(next.account, next.intent).kind).toBe("choose");
    expect(next.notice).toBe("The sign-in was turned down.");
  });
});

describe("afterPress", () => {
  it("opens the page at once when the code is already here", () => {
    const { next, open } = afterPress({ account: pending(), intent: null, notice: "old", opened: false }, "new");
    expect(open).toBe("https://app.eggie.io/signup?redirect=%2Fdevice%3Fuser_code%3DABCD-EFGH");
    expect(next).toMatchObject({ intent: "new", opened: true, notice: null });
  });

  it("opens nothing, and does not claim to have, while there is no code yet", () => {
    const { next, open } = afterPress({ account: { state: "signed_out", error: null }, intent: null, notice: null, opened: true }, "existing");
    expect(open).toBeNull();
    expect(next.opened).toBe(false);
  });
});
