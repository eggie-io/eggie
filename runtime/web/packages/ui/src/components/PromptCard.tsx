import type { ReactNode } from "react";
import { COPY_REFUSED, useCopy } from "../useCopy";
import { Button } from "./Button";
import { Card } from "./Card";
import { Icon } from "./Icon";
import s from "./PromptCard.module.css";

export function PromptCard({
  prompt,
  title = "Copy this prompt",
  hint = "Paste it into your coding agent and press enter. That's the whole job.",
  aside,
}: {
  prompt: string;
  title?: string;
  hint?: ReactNode;
  aside?: ReactNode;
}) {
  const { copied, refused, copy, target } = useCopy<HTMLPreElement>(prompt);

  return (
    <Card as="section" className={s.card}>
      <header className={s.head}>
        <span className={s.title}>{title}</span>
        {aside && <span className={s.aside}>{aside}</span>}
      </header>
      <div className={s.body}>
        <pre ref={target} className={s.prompt}>{prompt}</pre>
        <div className={s.row}>
          {copied ? (
            <span className={s.copied} role="status"><Icon name="check-circle" />Copied. It's yours.</span>
          ) : (
            <Button variant="primary" icon="copy" onClick={copy}>Copy the prompt</Button>
          )}
          <span className={s.hint} role={refused ? "alert" : undefined}>
            {refused ? COPY_REFUSED : hint}
          </span>
        </div>
      </div>
    </Card>
  );
}
