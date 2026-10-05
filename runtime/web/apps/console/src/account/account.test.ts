import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { signInLink, startError } from "./account";

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
