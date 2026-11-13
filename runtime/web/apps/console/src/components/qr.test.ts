import { describe, expect, it } from "vitest";
import { qrModules } from "./qr";

describe("qrModules", () => {
  it("draws an Expo address", () => {
    const qr = qrModules("exp://192.168.1.5:8081");
    expect(qr?.box).toBeGreaterThan(0);
    expect(qr?.d).toMatch(/^M\d/);
  });

  it("encodes non-ASCII text as UTF-8, not the low byte of each character", () => {
    expect(qrModules("П")?.d).not.toBe(qrModules("\u001f")?.d);
  });

  it("gives nothing for text too long for any QR code, instead of throwing", () => {
    expect(qrModules("x".repeat(5000))).toBeNull();
  });
});
