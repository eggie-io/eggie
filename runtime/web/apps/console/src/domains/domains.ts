import type { ProjectDomain } from "../projects/types";

export type DomainStatus = "waiting_dns" | "issuing" | "active" | "problem";

export interface DnsRecord {
  type: string;
  name: string;
  value: string;
}

export interface DomainProblem {
  code: string;
  message: string;
  next_step?: string;
  seen?: string[];
  expected?: string[];
}

export type Address =
  | { kind: "eggie"; hostname: string; url: string; serving: "direct" | "redirects" }
  | {
      kind: "custom";
      hostname: string;
      display: string;
      primary: boolean;
      status: string;
      serving: "direct" | "redirects" | "none";
      problem: DomainProblem | null;
      warning: DomainProblem | null;
      dns: DnsRecord[];
      checked_at: string | null;
      next_check_at: string | null;
    };

export type CustomAddress = Extract<Address, { kind: "custom" }>;

export interface AddressList {
  addresses: Address[];
  suggested?: string | null;
}

const LABEL: Record<DomainStatus, string> = {
  waiting_dns: "Waiting for DNS",
  issuing: "Getting certificate",
  active: "Active",
  problem: "Problem",
};

// The service owns the statuses; one this console doesn't know shows as the service sent it.
export function statusLabel(status: string): string {
  return Object.hasOwn(LABEL, status) ? LABEL[status as DomainStatus] : status;
}

export function badgeTone(status: string): "ok" | "busy" | "warning" {
  if (status === "active") return "ok";
  if (status === "problem") return "warning";
  return "busy";
}

export function showsDns(a: Address): boolean {
  return a.kind === "custom" && (a.status === "waiting_dns" || a.status === "problem" || a.warning?.code === "dns_moved");
}

// Only the serving primary is the project's address: a secondary serving while the primary
// has a problem redirects again once it recovers.
export function showsHint(a: Address): boolean {
  return a.kind === "custom" && a.status === "active" && a.primary && a.serving === "direct";
}

export const customRows =(list: AddressList): CustomAddress[] =>
  list.addresses.filter((a): a is CustomAddress => a.kind === "custom");

export function eggieNote(list: AddressList): string {
  const eggie = list.addresses.find((a) => a.kind === "eggie");
  const primary = customRows(list).find((a) => a.primary);
  return eggie?.serving === "redirects" && primary ? `Eggie address · redirects to ${primary.display}` : "Eggie address";
}

const FAST_MS = 5000;
const SLOW_MS = 60_000;

// An active domain whose certificate this server hasn't installed yet serves nothing so far.
export function pollMs(list: AddressList | undefined): number {
  return list && customRows(list).some((a) => a.status !== "active" || a.serving === "none") ? FAST_MS : SLOW_MS;
}

type Store = Pick<Storage, "getItem" | "setItem">;

// Storage can be blocked or throw on access; the hint then shows on every visit.
function localStore(): Store | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

const hintKey = (id: string, hostname: string) => `eggie.domainHint.${id}.${hostname}`;

export function hintDismissed(id: string, hostname: string, store = localStore()): boolean {
  try {
    return store !== null && store.getItem(hintKey(id, hostname)) !== null;
  } catch {
    return false;
  }
}

export function dismissHint(id: string, hostname: string, store = localStore()): void {
  try {
    store?.setItem(hintKey(id, hostname), "1");
  } catch {
    // The hint goes for this visit only.
  }
}

export function menuNote(domains: ProjectDomain[] | undefined): string | null {
  return domains?.find((d) => d.primary)?.display ?? null;
}

export const primaryFirst = (domains: ProjectDomain[] | undefined): ProjectDomain[] => {
  const all = domains ?? [];
  return [...all.filter((d) => d.primary), ...all.filter((d) => !d.primary)];
};

export const overviewRows = (domains: ProjectDomain[] | undefined): { host: string; label: string }[] =>
  primaryFirst(domains).map((d) => ({ host: d.display, label: statusLabel(d.status) }));
