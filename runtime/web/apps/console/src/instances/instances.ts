export type Instance = {
  id: string;
  display_name: string;
  console_hostname: string;
  status: string;
  signed_out: boolean;
  is_owner: boolean;
  current: boolean;
};

export type SwitcherEntry = {
  key: string;
  label: string;
  note: string | null;
  current: boolean;
  target: { host: string } | null;
};

function instanceEntry(instance: Instance): SwitcherEntry {
  const base = { key: instance.id, label: instance.display_name, current: instance.current };
  if (instance.current) return { ...base, note: null, target: null };
  const closed = (note: string): SwitcherEntry => ({ ...base, note, target: null });
  if (!instance.is_owner) return closed("Owned by a teammate");
  if (instance.status === "failed") return closed("Failed, see the web app");
  if (instance.status !== "running") return closed("Starting…");
  if (instance.signed_out) return closed("Set up again in the web app");
  return { ...base, note: null, target: { host: instance.console_hostname } };
}

// An instance only ever opens in a browser, which has no local VM to return to.
export function switcherEntries({ instances, site }: { instances: Instance[]; site: string | null | undefined }): SwitcherEntry[] {
  const here: SwitcherEntry[] =
    site === null ? [{ key: "local", label: "This computer", note: null, current: true, target: null }] : [];
  return [...here, ...instances.map(instanceEntry)];
}
