import type { Project } from "../projects/types";

export type TargetKitchen = {
  id: string;
  display_name: string;
  console_hostname: string;
  site_hostname: string;
  readiness: string;
  is_owner: boolean;
  open_console_url: string;
  manage_url: string;
};

export type PushRecord = {
  pushed_at: number;
  console_hostname: string;
  kitchen_project: string | null;
  urls: string[];
  secrets_missing: number;
};

export type PushTargets = {
  kitchens: TargetKitchen[];
  create_kitchen_url: string;
  pushed: Record<string, PushRecord>;
  databases: { service: string; image: string; floating: boolean }[];
  running: boolean;
};

export type KitchenRow = {
  id: string;
  label: string;
  host: string;
  note: string | null;
  pickable: boolean;
  pushedAt: number | null;
  manageUrl: string | null;
};

function rowNote(k: TargetKitchen): { note: string | null; manageUrl: string | null } {
  if (!k.is_owner) return { note: "Owned by a teammate", manageUrl: null };
  switch (k.readiness) {
    case "ready":
      return { note: null, manageUrl: null };
    case "starting":
      return { note: "Starting…", manageUrl: null };
    case "failed":
      return { note: "Failed", manageUrl: k.manage_url };
    case "signed_out":
      return { note: "Signed out", manageUrl: k.manage_url };
    default:
      return { note: "Not available", manageUrl: null };
  }
}

export function kitchenRows(t: PushTargets): KitchenRow[] {
  return t.kitchens.map((k) => {
    const { note, manageUrl } = rowNote(k);
    return {
      id: k.id,
      label: k.display_name,
      host: k.console_hostname,
      note,
      pickable: k.is_owner && k.readiness === "ready",
      pushedAt: t.pushed[k.id]?.pushed_at ?? null,
      manageUrl,
    };
  });
}

export function defaultData(row: KitchenRow): boolean {
  return row.pushedAt === null;
}

// A repeat push with data overwrites the kitchen's data, so it waits for an explicit confirmation.
export function pushChoice(row: KitchenRow, data: boolean, confirmed: boolean) {
  const repeat = row.pushedAt !== null;
  const needsConfirm = repeat && data;
  return { repeat, needsConfirm, canPush: row.pickable && (!needsConfirm || confirmed) };
}

export function secretsUrl(openConsoleUrl: string, kitchenProject: string): string {
  return `${openConsoleUrl}&return_path=${encodeURIComponent(`/p/${kitchenProject}/secrets`)}`;
}

export function floatingWarnings(t: PushTargets): string[] {
  return t.databases
    .filter((d) => d.floating)
    .map(
      (d) =>
        `${d.service} uses ${d.image}. The kitchen may download a newer version that can't read this data. Pin a version, e.g. ${pinExample(d.image)}.`,
    );
}

function pinExample(image: string): string {
  const name = image.split(":")[0].split("/").pop() ?? image;
  return `${name}:16`;
}

export function menuPush(
  project: Pick<Project, "empty" | "push">,
  online: boolean,
  pushingPercent: number | null,
): { note: string | null; disabled: boolean } | null {
  if (project.push.blocked === "site") return null;
  if (project.push.blocked === "signed_out") return { note: "Account required", disabled: true };
  if (!online) return { note: "Offline", disabled: true };
  if (project.empty) return { note: "Nothing to push yet", disabled: true };
  if (pushingPercent !== null) return { note: `Pushing… ${Math.round(pushingPercent)}%`, disabled: false };
  const last = project.push.last;
  return { note: last ? last.console_hostname : null, disabled: false };
}
