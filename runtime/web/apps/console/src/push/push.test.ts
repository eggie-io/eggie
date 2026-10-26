import { describe, expect, it } from "vitest";
import {
  floatingWarnings,
  instanceRows,
  menuPush,
  pushChoice,
  secretsUrl,
  type PushTargets,
  type TargetInstance,
} from "./push";
import type { Project } from "../projects/types";

const instance = (id: string, over: Partial<TargetInstance> = {}): TargetInstance => ({
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

const targets = (instances: TargetInstance[], over: Partial<PushTargets> = {}): PushTargets => ({
  instances,
  create_instance_url: "https://app.eggie.io/kitchens/new",
  pushed: {},
  floating: [],
  stops: false,
  ...over,
});

describe("instanceRows", () => {
  it("only ready instances the user owns can be picked", () => {
    const rows = instanceRows(
      targets([
        instance("a"),
        instance("b", { is_owner: false }),
        instance("c", { readiness: "starting" }),
        instance("d", { readiness: "failed" }),
        instance("e", { readiness: "signed_out" }),
        instance("f", { readiness: "weird" }),
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

  it("a pushed instance's row carries when it was pushed", () => {
    const [fresh, pushed] = instanceRows(
      targets([instance("a"), instance("b")], {
        pushed: { b: { pushed_at: 1700000000, console_hostname: "b.eggie.io", instance_project: "p", urls: [], secrets_missing: 0 } },
      }),
    );
    expect(fresh.pushedAt).toBeNull();
    expect(pushed.pushedAt).toBe(1700000000);
  });
});

describe("pushChoice", () => {
  const base = { id: "a", label: "a", host: "a", note: null, pickable: true, manageUrl: null };
  const freshRow = { ...base, pushedAt: null };
  const pushedRow = { ...base, pushedAt: 5 };

  it("a repeat push needs the replace confirmation; a first push does not", () => {
    expect(pushChoice(pushedRow, false)).toEqual({ repeat: true, canPush: false });
    expect(pushChoice(pushedRow, true).canPush).toBe(true);
    expect(pushChoice(freshRow, false)).toEqual({ repeat: false, canPush: true });
  });

  it("an unpickable row can never push", () => {
    expect(pushChoice({ ...pushedRow, pickable: false }, true).canPush).toBe(false);
  });
});

it("secretsUrl appends an encoded return path", () =>
  expect(secretsUrl("https://app.eggie.io/kitchens/open?host=k.eggie.io", "my-shop")).toBe(
    "https://app.eggie.io/kitchens/open?host=k.eggie.io&return_path=%2Fp%2Fmy-shop%2Fsecrets",
  ));

describe("floatingWarnings", () => {
  it("names each image with a pin suggestion", () => {
    const t = targets([], {
      floating: [
        { service: "db", image: "postgres:latest" },
        { service: "q", image: "rabbitmq" },
      ],
    });
    expect(floatingWarnings(t)).toEqual([
      "db uses postgres:latest. The kitchen may download a newer version that can't read this data. Pin a version, e.g. postgres:16.",
      "q uses rabbitmq. The kitchen may download a newer version that can't read this data. Pin a version tag, e.g. rabbitmq:<version>.",
    ]);
  });
});

describe("menuPush", () => {
  const project = (over: Partial<Pick<Project, "empty" | "push">> = {}): Pick<Project, "empty" | "push"> => ({
    empty: false,
    push: { blocked: null, last: null },
    ...over,
  });

  it("is hidden on a site", () => {
    expect(menuPush(project({ push: { blocked: "site", last: null } }), true, null)).toBeNull();
  });

  it("explains why it is disabled, in order", () => {
    const signedOut = project({ push: { blocked: "signed_out", last: null }, empty: true });
    expect(menuPush(signedOut, false, null)).toEqual({ note: "Account required", disabled: true });
    expect(menuPush(project({ empty: true }), false, null)).toEqual({ note: "Offline", disabled: true });
    expect(menuPush(project({ empty: true }), true, null)).toEqual({ note: "Nothing to push yet", disabled: true });
  });

  it("shows progress while pushing and the last instance otherwise", () => {
    expect(menuPush(project({ empty: true }), false, 40)).toEqual({ note: "Pushing… 40%", disabled: false });
    expect(menuPush(project(), true, 40)).toEqual({ note: "Pushing… 40%", disabled: false });
    const last = { instance_id: "i", console_hostname: "k.eggie.io", pushed_at: 1 };
    expect(menuPush(project({ push: { blocked: null, last } }), true, null)).toEqual({ note: "k.eggie.io", disabled: false });
    expect(menuPush(project(), true, null)).toEqual({ note: null, disabled: false });
  });
});
