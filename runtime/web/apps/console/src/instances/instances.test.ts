import { describe, expect, it } from "vitest";
import { switcherEntries } from "./instances";

const k = (over = {}) => ({ id: "i1", display_name: "Foo", console_hostname: "foo.eggie.io", status: "running", signed_out: false, is_owner: true, current: false, ...over });
const home = "http://127.0.0.1:5123";

describe("switcherEntries", () => {
  it("local VM in the desktop: This computer current, then kitchens", () =>
    expect(switcherEntries({ kitchens: [k()], site: null, desktopHome: home }).map((e) => [e.label, e.current, e.target?.kind ?? null]))
      .toEqual([["This computer", true, null], ["Foo", false, "kitchen"]]));
  it("local VM in a browser: This computer still labels where you are, not clickable", () =>
    expect(switcherEntries({ kitchens: [k()], site: null, desktopHome: null })[0]).toMatchObject({ label: "This computer", current: true, target: null }));
  it("kitchen in the desktop: This computer leads home, this kitchen is current", () =>
    expect(switcherEntries({ kitchens: [k({ current: true })], site: "foo.eggie.io", desktopHome: home }).map((e) => [e.label, e.current, e.target?.kind ?? null]))
      .toEqual([["This computer", false, "local"], ["Foo", true, null]]));
  it("kitchen in a browser: no This computer", () =>
    expect(switcherEntries({ kitchens: [k({ current: true })], site: "foo.eggie.io", desktopHome: null }).map((e) => e.label)).toEqual(["Foo"]));
  it.each([
    [{ is_owner: false }, "Owned by a teammate"],
    [{ status: "provisioning" }, "Starting…"],
    [{ status: "booting" }, "Starting…"],
    [{ status: "failed" }, "Failed, see the web app"],
    [{ is_owner: false, status: "failed" }, "Owned by a teammate"],
    [{ status: "deleting" }, "Starting…"],
    [{ signed_out: true }, "Set up again in the web app"],
  ])("a kitchen %o is shown with %s and cannot be opened", (over, note) =>
    expect(switcherEntries({ kitchens: [k(over)], site: null, desktopHome: null })[1]).toMatchObject({ note, target: null }));
  it("a running kitchen you own opens by its host", () =>
    expect(switcherEntries({ kitchens: [k()], site: null, desktopHome: null })[1].target).toEqual({ kind: "kitchen", host: "foo.eggie.io" }));
});
