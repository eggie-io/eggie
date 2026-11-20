import { describe, expect, it } from "vitest";
import { customRows, eggieNote, menuNote, overviewRows, pollMs, showsDns, showsHint, statusLabel, type Address, type AddressList } from "./domains";

const eggie = (serving: "direct" | "redirects"): Address => ({
  kind: "eggie", hostname: "shop.foo.eggie.space", url: "https://shop.foo.eggie.space", serving,
});

const custom = (hostname: string, status: string, over: Partial<Extract<Address, { kind: "custom" }>> = {}): Address => ({
  kind: "custom", hostname, display: hostname, primary: false, status, serving: "none", problem: null, warning: null,
  dns: [{ type: "CNAME", name: "www", value: "shop.foo.eggie.space" }], checked_at: null, next_check_at: null, ...over,
});

const listWithServingPrimary: AddressList = {
  addresses: [custom("example.com", "active", { primary: true, serving: "direct" }), custom("www.example.com", "active", { serving: "redirects" }), eggie("redirects")],
};
const listWithProblemPrimary: AddressList = {
  addresses: [custom("example.com", "problem", { primary: true }), eggie("direct")],
};

describe("statusLabel", () => {
  it("labels the four statuses and passes an unknown one through", () => {
    expect(["waiting_dns", "issuing", "active", "problem", "paused"].map(statusLabel))
      .toEqual(["Waiting for DNS", "Getting certificate", "Active", "Problem", "paused"]);
  });
});

describe("eggieNote", () => {
  it("says where the Eggie address redirects", () => {
    expect(eggieNote(listWithServingPrimary)).toBe("Eggie address · redirects to example.com");
    expect(eggieNote(listWithProblemPrimary)).toBe("Eggie address");
  });

  it("names the primary by its display form, not its ASCII hostname", () => {
    const list: AddressList = {
      addresses: [custom("xn--bcher-kva.example", "active", { display: "bücher.example", primary: true, serving: "direct" }), eggie("redirects")],
    };
    expect(eggieNote(list)).toBe("Eggie address · redirects to bücher.example");
  });
});

describe("showsDns", () => {
  it("shows DNS records only while they are needed", () => {
    expect(["waiting_dns", "problem", "issuing", "active"].map((status) => showsDns(custom("example.com", status))))
      .toEqual([true, true, false, false]);
    expect(showsDns(eggie("direct"))).toBe(false);
  });

  it("shows them for an active domain only when its warning says DNS moved", () => {
    const warned = (code: string) => custom("example.com", "active", { serving: "direct", warning: { code, message: "m" } });
    expect(["dns_moved", "renewal_failing", "something_new"].map((code) => showsDns(warned(code))))
      .toEqual([true, false, false]);
  });
});

describe("showsHint", () => {
  it("offers the address prompt only for a domain that serves the project itself", () => {
    const [primary, redirecting] = customRows(listWithServingPrimary);
    expect(showsHint(primary)).toBe(true);
    expect(showsHint(redirecting)).toBe(false);
    expect(showsHint(custom("example.com", "issuing"))).toBe(false);
  });
});

describe("pollMs", () => {
  it("polls fast until every custom domain is active", () => {
    expect(pollMs({ addresses: [custom("example.com", "active"), custom("www.example.com", "issuing"), eggie("direct")] })).toBe(5000);
    expect(pollMs(listWithServingPrimary)).toBe(60_000);
    expect(pollMs({ addresses: [eggie("direct")] })).toBe(60_000);
    expect(pollMs(undefined)).toBe(60_000);
  });

  it("keeps polling fast while an active domain's certificate is not installed here yet", () => {
    expect(pollMs({ addresses: [custom("example.com", "active", { primary: true }), eggie("direct")] })).toBe(5000);
  });
});

describe("menuNote", () => {
  it("names the primary domain in the menu", () => {
    expect(menuNote([
      { hostname: "www.example.com", display: "www.example.com", status: "waiting_dns", primary: false },
      { hostname: "xn--bcher-kva.example", display: "bücher.example", status: "active", primary: true },
    ])).toBe("bücher.example");
    expect(menuNote([])).toBeNull();
    expect(menuNote(undefined)).toBeNull();
  });
});

describe("overviewRows", () => {
  it("lists each domain with its label for Overview, primary first", () => {
    expect(overviewRows([{ hostname: "www.example.com", display: "www.example.com", status: "waiting_dns", primary: false },
                         { hostname: "example.com", display: "example.com", status: "active", primary: true }]))
      .toEqual([{ host: "example.com", label: "Active" }, { host: "www.example.com", label: "Waiting for DNS" }]);
    expect(overviewRows(undefined)).toEqual([]);
  });
});
