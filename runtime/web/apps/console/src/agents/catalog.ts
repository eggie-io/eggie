import { parseRich, type Rich } from "./rich";

export interface Media { kind: "image" | "video"; src: string }
// `media` is empty for a step that shows a placeholder; more than one makes a slider.
export interface Step { title: string; body: Rich[]; media: Media[]; alt: string }
// One line of the card: a label and a template over the API's SSH facts, with
// an optional explanation shown on demand.
// `copy` marks a plain-text value that is a value to paste, not guidance.
export interface CardField { label: string; value: string; hint?: string; copy?: true }
// `card` is null for a guide with nothing to copy.
export interface Guide { card: CardField[] | null; tagline: string; warning: Rich[] | null; steps: Step[] }
// `note` says where the agent lives, shown beside its name in lists; null when the manifest has none.
// `guide` is the manifest's `platforms.cloud` block, null when it has none.
export interface Agent { id: string; name: string; note: string | null; icon: string; guide: Guide | null; hasSetup: boolean }
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

function parseStep(value: unknown, folder: string): Step | null {
  if (!isObject(value) || !text(value.title) || !text(value.body) || !text(value.alt)) return null;
  const body = parseRich(value.body);
  const files = value.media ?? [];
  if (body === null || !Array.isArray(files)) return null;
  const media = files.map((file) => parseMedia(file, folder));
  if (media.some((item) => item === null)) return null;
  return { title: value.title, body, alt: value.alt, media: media as Media[] };
}

function parseCardField(value: unknown): CardField | null {
  if (!isObject(value) || !text(value.label) || !text(value.value)) return null;
  if (value.hint !== undefined && !text(value.hint)) return null;
  if (value.copy !== undefined && value.copy !== true) return null;
  const names = [...value.value.matchAll(PLACEHOLDER)].map((match) => match[1]);
  if (!names.every((name) => (SSH_FACTS as readonly string[]).includes(name))) return null;
  return {
    label: value.label, value: value.value,
    ...(value.hint !== undefined && { hint: value.hint }),
    ...(value.copy === true && { copy: true as const }),
  };
}

// Null when absent, undefined when present but wrong.
function parseCard(value: unknown): CardField[] | null | undefined {
  if (value === undefined) return null;
  if (!Array.isArray(value)) return undefined;
  const fields = value.map(parseCardField);
  if (fields.length === 0 || fields.some((field) => field === null)) return undefined;
  return fields as CardField[];
}

function parseGuide(value: unknown, folder: string): Guide | null {
  if (!isObject(value) || !text(value.tagline)) return null;
  const card = parseCard(value.card);
  if (card === undefined) return null;
  let warning: Rich[] | null = null;
  if (value.warning !== undefined) {
    warning = text(value.warning) ? parseRich(value.warning) : null;
    if (warning === null) return null;
  }
  const steps = parseSteps(value.steps, folder);
  return steps === null ? null : { card, tagline: value.tagline, warning, steps };
}

function parseSteps(value: unknown, folder: string): Step[] | null {
  if (!Array.isArray(value)) return null;
  const steps = value.map((step) => parseStep(step, folder));
  return steps.length === 0 || steps.some((step) => step === null) ? null : (steps as Step[]);
}

export function parseAgent(id: string, value: unknown, base: string): Agent | null {
  if (!ID.test(id) || !isObject(value) || !text(value.name) || !path(value.icon) || !isObject(value.platforms)) {
    return null;
  }
  if (value.note !== undefined && !text(value.note)) return null;
  const folder = `${base}/${id}`;
  let guide: Guide | null = null;
  if ("cloud" in value.platforms) {
    guide = parseGuide(value.platforms.cloud, folder);
    if (guide === null) return null;
  }
  return { id, name: value.name, note: value.note ?? null, icon: `${folder}/${value.icon}`, guide, hasSetup: isObject(value.setup) };
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
