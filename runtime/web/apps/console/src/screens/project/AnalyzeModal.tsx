import { Button, Modal, PromptCard } from "@eggie/ui";
import { analyzePrompt } from "../../projects/prompts";
import s from "./ProjectPage.module.css";

export function AnalyzeModal({ id, open, onClose }: { id: string; open: boolean; onClose: () => void }) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Ask for a once-over"
      wide
      footer={
        <>
          <span className={s.footNote}>Nothing was sent anywhere. This is just words on your clipboard.</span>
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <p className={s.modalSub}>
        Eggie doesn't read your code — your coding agent does. Here's the ask, written so you get a plain-language
        answer back.
      </p>
      <PromptCard
        prompt={analyzePrompt("full", id, null)}
        aside="for Claude Code, Codex, whoever's cooking"
        hint="Now paste it into your coding agent and press enter."
      />
    </Modal>
  );
}
