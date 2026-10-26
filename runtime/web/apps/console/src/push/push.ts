import type { Project } from "../projects/types";

export type TargetInstance = {
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
  instance_project: string | null;
  urls: string[];
  secrets_missing: number;
};

export type PushTargets = {
  instances: TargetInstance[];
  create_instance_url: string;
  pushed: Record<string, PushRecord>;
  floating: { service: string; image: string }[];
  // The project is up and has volumes to copy, so the push stops it for a while.
  stops: boolean;
};

export type InstanceRow = {
  id: string;
  label: string;
  host: string;
  note: string | null;
  pickable: boolean;
  pushedAt: number | null;
  manageUrl: string | null;
};

function rowNote(k: TargetInstance): { note: string | null; manageUrl: string | null } {
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

export function instanceRows(t: PushTargets): InstanceRow[] {
  return t.instances.map((k) => {
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

// A repeat push replaces the instance's files and data, so it waits for an explicit confirmation.
export function pushChoice(row: InstanceRow, confirmed: boolean) {
  const repeat = row.pushedAt !== null;
  return { repeat, canPush: row.pickable && (!repeat || confirmed) };
}

export function secretsUrl(openConsoleUrl: string, instanceProject: string): string {
  return `${openConsoleUrl}&return_path=${encodeURIComponent(`/p/${instanceProject}/secrets`)}`;
}

export function floatingWarnings(t: PushTargets): string[] {
  return t.floating.map(
    (d) => `${d.service} uses ${d.image}. The kitchen may download a newer version that can't read this data. ${pinAdvice(d.image)}`,
  );
}

function pinAdvice(image: string): string {
  const name = image.split(":")[0].split("/").pop() ?? image;
  return name.startsWith("postgres")
    ? "Pin a version, e.g. postgres:16."
    : `Pin a version tag, e.g. ${name}:<version>.`;
}

export function menuPush(
  project: Pick<Project, "empty" | "push">,
  online: boolean,
  pushingPercent: number | null,
): { note: string | null; disabled: boolean } | null {
  if (project.push.blocked === "site") return null;
  if (project.push.blocked === "signed_out") return { note: "Account required", disabled: true };
  if (pushingPercent !== null) return { note: `Pushing… ${Math.round(pushingPercent)}%`, disabled: false };
  if (!online) return { note: "Offline", disabled: true };
  if (project.empty) return { note: "Nothing to push yet", disabled: true };
  const last = project.push.last;
  return { note: last ? last.console_hostname : null, disabled: false };
}
