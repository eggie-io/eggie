import { useEffect, useState } from "react";
import { Button } from "@eggie/ui";

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "refused">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [state]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("refused");
    }
  };
  return (
    <Button variant="quiet" className={className} onClick={copy}>
      <span role="status" aria-live="polite">{state === "copied" ? "Copied" : state === "refused" ? "Couldn't copy" : "Copy"}</span>
    </Button>
  );
}
