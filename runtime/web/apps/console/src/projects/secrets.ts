// Mirrors the API's check_name so a bad name is caught before the round trip.
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const RESERVED = /^(COMPOSE_|DOCKER_)/i;

export function nameProblem(raw: string, taken: string[]): string | null {
  const name = raw.trim();
  if (name === "") return "Give it a name, like OPENAI_API_KEY.";
  if (!NAME.test(name)) return "Use letters, digits and underscores, not starting with a digit.";
  if (RESERVED.test(name)) return "Names starting with COMPOSE_ or DOCKER_ are reserved.";
  if (taken.includes(name)) return `There's already a secret called ${name} — use Edit to change it.`;
  return null;
}
