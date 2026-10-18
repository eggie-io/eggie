import { describe, expect, it } from "vitest";
import { switcherEntries } from "./instances";

const k = (over = {}) => ({ id: "i1", display_name: "Foo", console_hostname: "foo.eggie.io", status: "running", signed_out: false, is_owner: true, current: false, ...over });

describe("switcherEntries", () => {
  it("local VM: This computer labels where you are, then kitchens", () =>
    expect(switcherEntries({ kitchens: [k()], site: null }).map((e) => [e.label, e.current, e.target?.host ?? null]))
      .toEqual([["This computer", true, null], ["Foo", false, "foo.eggie.io"]]));
  it("kitchen: no This computer, this kitchen is current and not clickable", () =>
    expect(switcherEntries({ kitchens: [k({ current: true })], site: "foo.eggie.io" }).map((e) => [e.label, e.current, e.target]))
      .toEqual([["Foo", true, null]]));
  it.each([
    [{ is_owner: false }, "Owned by a teammate"],
    [{ status: "provisioning" }, "Starting…"],
    [{ status: "booting" }, "Starting…"],
    [{ status: "failed" }, "Failed, see the web app"],
    [{ is_owner: false, status: "failed" }, "Owned by a teammate"],
    [{ status: "deleting" }, "Starting…"],
    [{ signed_out: true }, "Set up again in the web app"],
  ])("a kitchen %o is shown with %s and cannot be opened", (over, note) =>
    expect(switcherEntries({ kitchens: [k(over)], site: null })[1]).toMatchObject({ note, target: null }));
});
