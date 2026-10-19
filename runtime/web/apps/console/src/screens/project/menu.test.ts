import { describe, expect, it } from "vitest";
import { menuNotes } from "./menu";

const off = { public: { state: "off", note: null }, secrets_requested: 0 } as const;
const reason = { code: "not_registered", message: "This project isn't linked to your account yet. Try again in a minute." };

describe("menuNotes", () => {
  it("hides Public address on a site, and while it isn't known yet whether this is one", () => {
    expect(menuNotes(off, "foo.eggie.io", 0).public).toBeNull();
    expect(menuNotes(off, undefined, 0).public).toBeNull();
  });

  it("disables Public address when unavailable, with the API's reason as its note", () => {
    const notes = menuNotes({ ...off, public: { state: "unavailable", reason } }, null, 0);
    expect(notes.public).toEqual({ note: reason.message, disabled: true });
  });

  it("keeps Public address enabled in every other state", () => {
    expect(menuNotes(off, null, 0).public).toEqual({ note: "Off", disabled: false });
    expect(menuNotes({ ...off, public: { state: "failed", reason } }, null, 0).public).toEqual({
      note: "Didn't work",
      disabled: false,
    });
  });

  it("notes Secrets only when the project asks for some", () => {
    expect(menuNotes(off, null, 0).secrets).toBeNull();
    expect(menuNotes({ ...off, secrets_requested: 2 }, null, 0).secrets).toBe("2 requested");
  });
});
