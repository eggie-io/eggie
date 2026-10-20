import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "./Button";
import { Card } from "./Card";
import { Icon } from "./Icon";
import s from "./PromptCard.module.css";

const COPIED_MS = 2600;

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
  const [copied, setCopied] = useState(false);
  const [refused, setRefused] = useState(false);
  const body = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setRefused(false);
    } catch {
      // Clipboard access can be refused; leave the prompt selected for Ctrl+C.
      setRefused(true);
      const node = body.current;
      const selection = window.getSelection();
      if (!node || !selection) return;
      const range = document.createRange();
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  }

  return (
    <Card as="section" className={s.card}>
      <header className={s.head}>
        <span className={s.title}>{title}</span>
        {aside && <span className={s.aside}>{aside}</span>}
      </header>
      <div className={s.body}>
        <pre ref={body} className={s.prompt}>{prompt}</pre>
        <div className={s.row}>
          {copied ? (
            <span className={s.copied} role="status"><Icon name="check-circle" />Copied. It's yours.</span>
          ) : (
            <Button variant="primary" icon="copy" onClick={copy}>Copy the prompt</Button>
          )}
          <span className={s.hint} role={refused ? "alert" : undefined}>
            {refused ? "Couldn't copy — the text is selected, press Ctrl+C (⌘C on a Mac)." : hint}
          </span>
        </div>
      </div>
    </Card>
  );
}
