import { publicView, tileLine } from "../../projects/public";
import type { Project } from "../../projects/types";

export interface MenuNotes {
  secrets: string | null;
  // null hides the item.
  public: { note: string; disabled: boolean } | null;
}

// `site` is undefined until the first health answer on a non-local address:
// Public address stays hidden until we know this isn't a site.
export function menuNotes(
  project: Pick<Project, "public" | "secrets_requested">,
  site: string | null | undefined,
  nowMs: number,
): MenuNotes {
  const pub = publicView(project.public, nowMs);
  return {
    secrets: project.secrets_requested > 0 ? `${project.secrets_requested} requested` : null,
    public: site === null ? { note: tileLine(pub), disabled: pub.kind === "unavailable" } : null,
  };
}
