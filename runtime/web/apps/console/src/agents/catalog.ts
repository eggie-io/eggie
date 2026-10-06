import { parseRich, type Rich } from "./rich";

export type Platform = "windows" | "mac";
const PLATFORMS: Platform[] = ["windows", "mac"];

export interface Media { kind: "image" | "video"; src: string }
// `media` is empty for a step that shows a placeholder; more than one makes a slider.
export interface Step { title: string; body: Rich[]; media: Media[]; alt: string }
// One line of the SSH card: a label and a template over the API's SSH facts.
export interface SshField { label: string; value: string }
// `ssh` is null for a guide that does not connect over SSH (WSL).
export interface Guide { ssh: SshField[] | null; tagline: string; steps: Step[] }
export interface Agent { id: string; name: string; icon: string; platforms: Partial<Record<Platform, Guide>>; hasSetup: boolean }
export interface Connect {
  vm: "wsl" | "lima" | "other";
  ssh: { host: string; port: number; user: string; key_file: string } | null;
}

const ID = /^[a-z0-9][a-z0-9-]*$/;
// Relative and without "..": nginx serves the whole site from the same root.
const PATH = /^(?!.*\.\.)[A-Za-z0-9._-][A-Za-z0-9._/-]*$/;
// The facts a card value may name; anything else is a typo in the manifest.
export const SSH_FACTS = ["user", "host", "port", "key_file"] as const;
const PLACEHOLDER = /\{([^}]*)\}/g;
// A step's media files, by extension. nginx's mime.types knows all of them.
const KINDS: Record<string, Media["kind"]> = {
  png: "image", jpg: "image", jpeg: "image", webp: "image", gif: "image", svg: "image", avif: "image",
  mp4: "video", webm: "video",
};

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

function parseMedia(value: unknown, folder: string): Media | null {
  if (!path(value)) return null;
  const kind = KINDS[value.slice(value.lastIndexOf(".") + 1).toLowerCase()];
  return kind ? { kind, src: `${folder}/${value}` } : null;
}

function parseStep(value: unknown, folder: string): Step | null {
  if (!isObject(value) || !text(value.title) || !text(value.body) || !text(value.alt)) return null;
  const body = parseRich(value.body);
  const files = value.media ?? [];
  if (body === null || !Array.isArray(files)) return null;
  const media = files.map((file) => parseMedia(file, folder));
  if (media.some((item) => item === null)) return null;
  return { title: value.title, body, alt: value.alt, media: media as Media[] };
}

function parseSshField(value: unknown): SshField | null {
  if (!isObject(value) || !text(value.label) || !text(value.value)) return null;
  const names = [...value.value.matchAll(PLACEHOLDER)].map((match) => match[1]);
  if (!names.every((name) => (SSH_FACTS as readonly string[]).includes(name))) return null;
  return { label: value.label, value: value.value };
}

function parseGuide(value: unknown, folder: string): Guide | null {
  if (!isObject(value) || !text(value.tagline) || !Array.isArray(value.steps)) return null;
  let ssh: SshField[] | null = null;
  if (value.ssh !== undefined) {
    if (!Array.isArray(value.ssh)) return null;
    const fields = value.ssh.map(parseSshField);
    if (fields.length === 0 || fields.some((field) => field === null)) return null;
    ssh = fields as SshField[];
  }
  const steps = value.steps.map((step) => parseStep(step, folder));
  if (steps.length === 0 || steps.some((step) => step === null)) return null;
  return { ssh, tagline: value.tagline, steps: steps as Step[] };
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
