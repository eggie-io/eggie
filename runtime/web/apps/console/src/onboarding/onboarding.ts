import type { AgentStatuses } from "../agents/status";

export type Phase = "deciding" | "onboarding" | "off";

const KEY = "eggie.onboarded";

// A query that hasn't answered is undefined; one that failed is null.
export function decide({
  marked,
  onSite,
  statuses,
  counterItems,
}: {
  marked: boolean;
  // The desktop app onboards on its own; the console only does for a cloud kitchen.
  onSite: boolean;
  statuses: AgentStatuses | null | undefined;
  counterItems: number | null | undefined;
}): Phase {
  if (marked || !onSite) return "off";
  // Either answer alone is enough to skip; neither failing is a reason to onboard.
  if (statuses && Object.values(statuses.agents).some((agent) => agent?.connected)) return "off";
  if (counterItems) return "off";
  if (statuses === null || counterItems === null) return "off";
  if (statuses === undefined || counterItems === undefined) return "deciding";
  return "onboarding";
}

export function readMark(store: () => Storage | null): boolean {
  try {
    return store()?.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function writeMark(store: () => Storage | null): void {
  try {
    store()?.setItem(KEY, "1");
  } catch {
    // Without storage the next visit asks again, until an agent connects.
  }
}
