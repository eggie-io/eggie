import { Button, Modal, PromptCard } from "@eggie/ui";
import { ANALYZE_PROMPT } from "../../projects/prompts";
import s from "./ProjectPage.module.css";

export function AnalyzeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Ask for a once-over">
      <p className={s.modalSub}>
        Eggie doesn't read your code — your coding agent does. Here's the ask, written so you get a plain-language
        answer back.
      </p>
      <PromptCard
        prompt={ANALYZE_PROMPT}
        aside="for Claude Code, Codex, whoever's cooking"
        hint="Now paste it into your coding agent and press enter."
      />
      <div className={s.modalFoot}>
        <Button onClick={onClose}>Close</Button>
        <span className={s.note}>Nothing was sent anywhere. This is just words on your clipboard.</span>
      </div>
    </Modal>
  );
}
