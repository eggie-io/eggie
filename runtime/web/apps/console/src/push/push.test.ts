import { describe, expect, it } from "vitest";
import {
  defaultData,
  floatingWarnings,
  kitchenRows,
  menuPush,
  pushChoice,
  secretsUrl,
  type PushTargets,
  type TargetKitchen,
} from "./push";

const kitchen = (id: string, over: Partial<TargetKitchen> = {}): TargetKitchen => ({
  id,
  display_name: id,
  console_hostname: `${id}.eggie.io`,
  site_hostname: `${id}.site.io`,
  readiness: "ready",
  is_owner: true,
  open_console_url: "https://app.eggie.io/kitchens/open?host=k.eggie.io",
  manage_url: `https://app.eggie.io/kitchens/${id}`,
  ...over,
});

const targets = (kitchens: TargetKitchen[], over: Partial<PushTargets> = {}): PushTargets => ({
  kitchens,
  create_kitchen_url: "https://app.eggie.io/kitchens/new",
  pushed: {},
  databases: [],
  running: false,
  ...over,
});

describe("kitchenRows", () => {
  it("only ready kitchens the user owns can be picked", () => {
    const rows = kitchenRows(
      targets([
        kitchen("a"),
        kitchen("b", { is_owner: false }),
        kitchen("c", { readiness: "starting" }),
        kitchen("d", { readiness: "failed" }),
        kitchen("e", { readiness: "signed_out" }),
        kitchen("f", { readiness: "weird" }),
      ]),
    );
    expect(rows.map((r) => [r.pickable, r.note, r.manageUrl])).toEqual([
      [true, null, null],
      [false, "Owned by a teammate", null],
      [false, "Starting…", null],
      [false, "Failed", "https://app.eggie.io/kitchens/d"],
      [false, "Signed out", "https://app.eggie.io/kitchens/e"],
      [false, "Not available", null],
    ]);
  });

  it("a pushed kitchen defaults data off and marks the row", () => {
    const [fresh, pushed] = kitchenRows(
      targets([kitchen("a"), kitchen("b")], {
        pushed: { b: { pushed_at: 1700000000, console_hostname: "b.eggie.io", kitchen_project: "p", urls: [], secrets_missing: 0 } },
      }),
    );
    expect(fresh.pushedAt).toBeNull();
    expect(defaultData(fresh)).toBe(true);
    expect(pushed.pushedAt).toBe(1700000000);
    expect(defaultData(pushed)).toBe(false);
  });
});

describe("pushChoice", () => {
  const base = { id: "a", label: "a", host: "a", note: null, pickable: true, manageUrl: null };
  const freshRow = { ...base, pushedAt: null };
  const pushedRow = { ...base, pushedAt: 5 };

  it("data on a repeat push needs the confirmation", () => {
    expect(pushChoice(pushedRow, true, false)).toEqual({ repeat: true, needsConfirm: true, canPush: false });
    expect(pushChoice(pushedRow, true, true).canPush).toBe(true);
    expect(pushChoice(pushedRow, false, false).canPush).toBe(true);
    expect(pushChoice(freshRow, true, false)).toEqual({ repeat: false, needsConfirm: false, canPush: true });
  });

  it("an unpickable row can never push", () => {
    expect(pushChoice({ ...freshRow, pickable: false }, false, true).canPush).toBe(false);
  });
});

it("secretsUrl appends an encoded return path", () =>
  expect(secretsUrl("https://app.eggie.io/kitchens/open?host=k.eggie.io", "my-shop")).toBe(
    "https://app.eggie.io/kitchens/open?host=k.eggie.io&return_path=%2Fp%2Fmy-shop%2Fsecrets",
  ));

describe("floatingWarnings", () => {
  it("warns only for floating tags, with a pin suggestion", () => {
    const t = targets([], {
      databases: [
        { service: "db", image: "postgres:latest", floating: true },
        { service: "cache", image: "redis:7", floating: false },
      ],
    });
    expect(floatingWarnings(t)).toEqual([
      "db uses postgres:latest. The kitchen may download a newer version that can't read this data. Pin a version, e.g. postgres:16.",
    ]);
  });
});

describe("menuPush", () => {
  const project = (over: object = {}) => ({ empty: false, push: { blocked: null, last: null }, ...over }) as never;

  it("is hidden on a site", () => {
    expect(menuPush(project({ push: { blocked: "site", last: null } }), true, null)).toBeNull();
  });

  it("explains why it is disabled, in order", () => {
    const signedOut = project({ push: { blocked: "signed_out", last: null }, empty: true });
    expect(menuPush(signedOut, false, null)).toEqual({ note: "Account required", disabled: true });
    expect(menuPush(project({ empty: true }), false, null)).toEqual({ note: "Offline", disabled: true });
    expect(menuPush(project({ empty: true }), true, null)).toEqual({ note: "Nothing to push yet", disabled: true });
  });

  it("shows progress while pushing and the last kitchen otherwise", () => {
    expect(menuPush(project(), true, 40)).toEqual({ note: "Pushing… 40%", disabled: false });
    const last = { instance_id: "i", console_hostname: "k.eggie.io", pushed_at: 1 };
    expect(menuPush(project({ push: { blocked: null, last } }), true, null)).toEqual({ note: "k.eggie.io", disabled: false });
    expect(menuPush(project(), true, null)).toEqual({ note: null, disabled: false });
  });
});
