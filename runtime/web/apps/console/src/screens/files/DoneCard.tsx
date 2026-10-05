import { Button, PromptCard } from "@eggie/ui";
import { size } from "../../projects/format";
import { kindOf, promptFor } from "../../uploads/kinds";
import { joinPath } from "../../uploads/paths";
import type { UploadItem } from "../../uploads/queue";
import s from "./FilesPage.module.css";

export function DoneCard({ item, onDismiss }: { item: UploadItem; onDismiss: () => void }) {
  const kind = kindOf(item.name);
  if (!kind) return null;
  return (
    <section className={s.done}>
      <h2 className={s.doneTitle}>
        <code>{item.name}</code> is in — it landed in <code>{item.dir === "" ? "the top of the project" : `${item.dir}/`}</code>. All {size(item.size)} of it.
      </h2>
      <p className={s.doneLead}>
        <strong>One more step, and it's not yours to figure out.</strong>{" "}
        {kind === "dump"
          ? "A file sitting in a folder isn't a database yet. Hand this to your coding agent and it'll do the rest."
          : "An archive sitting in a folder isn't unpacked yet. Hand this to your coding agent and it'll do the rest."}
      </p>
      <PromptCard prompt={promptFor(kind, joinPath(item.dir, item.name))} aside="for Claude Code, Codex, whoever's cooking" />
      <div className={s.modalFoot}>
        <Button onClick={onDismiss}>Back to files</Button>
        <span className={s.note}>Or ignore all this — the file is in the folder either way.</span>
      </div>
    </section>
  );
}
