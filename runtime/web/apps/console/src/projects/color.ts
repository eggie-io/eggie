const TILES = ["var(--coral)", "var(--gold)", "var(--periwinkle)", "var(--orchid)"];

// Stable per id, so a project keeps its colour across reloads and screens.
export function projectColor(id: string): { bg: string; ink: string } {
  let sum = 0;
  for (let i = 0; i < id.length; i += 1) sum += id.charCodeAt(i);
  return { bg: TILES[sum % TILES.length], ink: "var(--on-gold)" };
}
