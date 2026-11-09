import { parseRich, type Rich } from "./rich";

export type Platform = "windows" | "mac" | "cloud";
const PLATFORMS: Platform[] = ["windows", "mac", "cloud"];

export interface Media { kind: "image" | "video"; src: string }
// `media` is empty for a step that shows a placeholder; more than one makes a slider.
// `action: "connect"` puts the desktop window's "Add Eggie to …" button in the step.
export interface Step { title: string; body: Rich[]; media: Media[]; alt: string; action: "connect" | null }
// One line of the card: a label and a template over the API's SSH facts, with
// an optional explanation shown on demand.
export interface CardField { label: string; value: string; hint?: string }
// `card` is null for a guide with nothing to copy. `auto` is the shorter list
// shown when the desktop window can add the connection itself, null when the
// manifest has none; `steps` stays the way to do it by hand.
export interface Guide { card: CardField[] | null; tagline: string; warning: Rich[] | null; steps: Step[]; auto: Step[] | null }
// `note` says where the agent lives, shown beside its name in lists; null when the manifest has none.
export interface Agent { id: string; name: string; note: string | null; icon: string; platforms: Partial<Record<Platform, Guide>>; hasSetup: boolean }
export interface Connect {
  vm: "wsl" | "lima" | "site" | "other";
  ssh: { host: string; port: number; user: string; key_file: string | null } | null;
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

function parseStep(value: unknown, folder: string, actions = false): Step | null {
  if (!isObject(value) || !text(value.title) || !text(value.body) || !text(value.alt)) return null;
  if (value.action !== undefined && !(actions && value.action === "connect")) return null;
  const body = parseRich(value.body);
  const files = value.media ?? [];
  if (body === null || !Array.isArray(files)) return null;
  const media = files.map((file) => parseMedia(file, folder));
  if (media.some((item) => item === null)) return null;
  return { title: value.title, body, alt: value.alt, media: media as Media[], action: value.action === "connect" ? "connect" : null };
}

function parseCardField(value: unknown): CardField | null {
  if (!isObject(value) || !text(value.label) || !text(value.value)) return null;
  if (value.hint !== undefined && !text(value.hint)) return null;
  const names = [...value.value.matchAll(PLACEHOLDER)].map((match) => match[1]);
  if (!names.every((name) => (SSH_FACTS as readonly string[]).includes(name))) return null;
  return { label: value.label, value: value.value, ...(value.hint !== undefined && { hint: value.hint }) };
}

function parseGuide(value: unknown, folder: string): Guide | null {
  if (!isObject(value) || !text(value.tagline)) return null;
  let card: CardField[] | null = null;
  if (value.card !== undefined) {
    if (!Array.isArray(value.card)) return null;
    const fields = value.card.map(parseCardField);
    if (fields.length === 0 || fields.some((field) => field === null)) return null;
    card = fields as CardField[];
  }
  let warning: Rich[] | null = null;
  if (value.warning !== undefined) {
    warning = text(value.warning) ? parseRich(value.warning) : null;
    if (warning === null) return null;
  }
  const steps = parseSteps(value.steps, folder, false);
  if (steps === null) return null;
  let auto: Step[] | null = null;
  if (value.auto !== undefined) {
    auto = isObject(value.auto) ? parseSteps(value.auto.steps, folder, true) : null;
    // Without exactly one button the list could not be followed.
    if (auto === null || auto.filter((step) => step.action === "connect").length !== 1) return null;
  }
  return { card, tagline: value.tagline, warning, steps, auto };
}

function parseSteps(value: unknown, folder: string, actions: boolean): Step[] | null {
  if (!Array.isArray(value)) return null;
  const steps = value.map((step) => parseStep(step, folder, actions));
  return steps.length === 0 || steps.some((step) => step === null) ? null : (steps as Step[]);
}

// The steps to show: the automatic list when the desktop window offers to
// connect this agent and the user has not asked to do it by hand.
export function guideSteps(guide: Guide, offered: boolean, byHand: boolean): { steps: Step[]; auto: boolean } {
  return guide.auto && offered && !byHand ? { steps: guide.auto, auto: true } : { steps: guide.steps, auto: false };
}

// Where the list opens: past the button once the connection is there, so
// coming back from the desktop window lands on what to do next.
export function firstStep(steps: Step[], added: boolean): number {
  const at = steps.findIndex((step) => step.action === "connect");
  return added && at !== -1 ? Math.min(at + 1, steps.length - 1) : 0;
}

export function parseAgent(id: string, value: unknown, base: string): Agent | null {
  if (!ID.test(id) || !isObject(value) || !text(value.name) || !path(value.icon) || !isObject(value.platforms)) {
    return null;
  }
  if (value.note !== undefined && !text(value.note)) return null;
  const folder = `${base}/${id}`;
  const platforms: Agent["platforms"] = {};
  for (const platform of PLATFORMS) {
    if (!(platform in value.platforms)) continue;
    const guide = parseGuide(value.platforms[platform], folder);
    if (guide === null) return null;
    platforms[platform] = guide;
  }
  return { id, name: value.name, note: value.note ?? null, icon: `${folder}/${value.icon}`, platforms, hasSetup: isObject(value.setup) };
}

export function platformFor(connect: Connect | undefined, userAgent: string): Platform {
  if (connect?.vm === "wsl") return "windows";
  if (connect?.vm === "lima") return "mac";
  if (connect?.vm === "site") return "cloud";
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
