export type Platform = "windows" | "mac";
const PLATFORMS: Platform[] = ["windows", "mac"];

export interface Step { title: string; body: string; screenshot: string | null; alt: string }
export interface Guide { viaSsh: boolean; tagline: string; steps: Step[] }
export interface Agent { id: string; name: string; icon: string; platforms: Partial<Record<Platform, Guide>>; hasSetup: boolean }
export interface Connect {
  vm: "wsl" | "lima" | "other";
  ssh: { host: string; port: number; user: string; key_file: string } | null;
}

const ID = /^[a-z0-9][a-z0-9-]*$/;
// Relative and without "..": nginx serves the whole site from the same root.
const PATH = /^(?!.*\.\.)[A-Za-z0-9._-][A-Za-z0-9._/-]*$/;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim() !== "";
const path = (value: unknown): value is string => typeof value === "string" && PATH.test(value);

export function parseIndex(value: unknown): string[] {
  const ids = isObject(value) ? value.agents : undefined;
  if (!Array.isArray(ids) || !ids.every((id) => typeof id === "string" && ID.test(id))) {
    throw new Error("the agent list is damaged");
  }
  return ids as string[];
}

function parseStep(value: unknown, folder: string): Step | null {
  if (!isObject(value) || !text(value.title) || !text(value.body) || !text(value.alt)) return null;
  const shot = value.screenshot ?? null;
  if (shot !== null && !path(shot)) return null;
  return { title: value.title, body: value.body, alt: value.alt, screenshot: shot === null ? null : `${folder}/${shot}` };
}

function parseGuide(value: unknown, folder: string): Guide | null {
  if (!isObject(value) || typeof value.via_ssh !== "boolean" || !text(value.tagline) || !Array.isArray(value.steps)) {
    return null;
  }
  const steps = value.steps.map((step) => parseStep(step, folder));
  if (steps.length === 0 || steps.some((step) => step === null)) return null;
  return { viaSsh: value.via_ssh, tagline: value.tagline, steps: steps as Step[] };
}

export function parseAgent(id: string, value: unknown, base: string): Agent | null {
  if (!ID.test(id) || !isObject(value) || !text(value.name) || !path(value.icon) || !isObject(value.platforms)) {
    return null;
  }
  const folder = `${base}/${id}`;
  const platforms: Agent["platforms"] = {};
  for (const platform of PLATFORMS) {
    if (!(platform in value.platforms)) continue;
    const guide = parseGuide(value.platforms[platform], folder);
    if (guide === null) return null;
    platforms[platform] = guide;
  }
  return { id, name: value.name, icon: `${folder}/${value.icon}`, platforms, hasSetup: isObject(value.setup) };
}

export function platformFor(connect: Connect | undefined, userAgent: string): Platform {
  if (connect?.vm === "wsl") return "windows";
  if (connect?.vm === "lima") return "mac";
  return /Mac/.test(userAgent) ? "mac" : "windows";
}

export async function loadCatalog(fetchJson: (url: string) => Promise<unknown>, base = "/agent-guides"): Promise<Agent[]> {
  const ids = parseIndex(await fetchJson(`${base}/index.json`));
  const agents = await Promise.all(
    ids.map(async (id) => {
      try {
        return parseAgent(id, await fetchJson(`${base}/${id}/agent.json`), base);
      } catch {
        return null;
      }
    }),
  );
  return agents.filter((agent): agent is Agent => agent !== null);
}
