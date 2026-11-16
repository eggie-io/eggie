import type { SecretsView } from "./types";

// Mirrors the API's check_name so a bad name is caught before the round trip.
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const RESERVED = /^(COMPOSE_|DOCKER_|LD_|BUILDX_|BUILDKIT_)|^(PATH|HOME)$/i;

export function nameProblem(raw: string, taken: string[]): string | null {
  const name = raw.trim();
  if (name === "") return "Give it a name, like OPENAI_API_KEY.";
  if (!NAME.test(name)) return "Use letters, digits and underscores, not starting with a digit.";
  if (RESERVED.test(name)) return "That name is reserved: Eggie and Docker read it themselves.";
  if (taken.includes(name)) return `There's already a secret called ${name} — use Replace to change it.`;
  return null;
}

export function splitSecrets(view: SecretsView) {
  return {
    used: view.secrets.filter((x) => !x.unused),
    unused: view.secrets.filter((x) => x.unused),
  };
}

export const MAX_FILE_BYTES = 64 * 1024;

export async function fileToBase64(file: File): Promise<string> {
  if (file.size > MAX_FILE_BYTES) throw new Error("A key file can be at most 64 KB.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

// The API wants a bare file name; some browsers/webviews report a path.
export const baseName = (name: string) => name.split(/[\\/]/).pop() ?? name;

export function fieldState(view: SecretsView | undefined, name: string, file: boolean) {
  const stored = view?.secrets.find((x) => x.name === name && (x.kind === "file") === file);
  return { saved: stored !== undefined, filename: stored?.filename ?? null, updatedAt: stored?.updated_at ?? null };
}
