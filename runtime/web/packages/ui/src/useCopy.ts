import { useEffect, useRef, useState } from "react";

const COPIED_MS = 2600;

// Copies `text`; when the clipboard is refused, selects `target`'s text so Ctrl+C still works.
export function useCopy<T extends HTMLElement>(text: string) {
  const [copied, setCopied] = useState(false);
  const [refused, setRefused] = useState(false);
  const target = useRef<T>(null);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setRefused(false);
    } catch {
      setRefused(true);
      const node = target.current;
      const selection = window.getSelection();
      if (!node || !selection) return;
      const range = document.createRange();
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  }

  return { copied, refused, copy, target };
}

export const COPY_REFUSED = "Couldn't copy — the text is selected, press Ctrl+C (⌘C on a Mac).";
