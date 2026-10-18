export type Kitchen = {
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

function kitchenEntry(kitchen: Kitchen): SwitcherEntry {
  const base = { key: kitchen.id, label: kitchen.display_name, current: kitchen.current };
  if (kitchen.current) return { ...base, note: null, target: null };
  const closed = (note: string): SwitcherEntry => ({ ...base, note, target: null });
  if (!kitchen.is_owner) return closed("Owned by a teammate");
  if (kitchen.status === "failed") return closed("Failed, see the web app");
  if (kitchen.status !== "running") return closed("Starting…");
  if (kitchen.signed_out) return closed("Set up again in the web app");
  return { ...base, note: null, target: { host: kitchen.console_hostname } };
}

// A kitchen only ever opens in a browser, which has no local VM to return to.
export function switcherEntries({ kitchens, site }: { kitchens: Kitchen[]; site: string | null | undefined }): SwitcherEntry[] {
  const here: SwitcherEntry[] =
    site === null ? [{ key: "local", label: "This computer", note: null, current: true, target: null }] : [];
  return [...here, ...kitchens.map(kitchenEntry)];
}
