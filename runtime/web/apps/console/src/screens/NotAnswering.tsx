import { useEffect } from "react";
import { Button, Egg } from "@eggie/ui";
import { StatusScreen } from "./StatusScreen";
import s from "./StatusScreen.module.css";

const RETRY_MS = 5000;

export function NotAnswering({ onRetry }: { onRetry: () => void }) {
  useEffect(() => {
    const timer = window.setInterval(onRetry, RETRY_MS);
    return () => window.clearInterval(timer);
  }, [onRetry]);

  return (
    <StatusScreen
      art={<Egg tone="cold" size={96} />}
      title="Eggie isn't answering"
      actions={<Button variant="primary" size="lg" onClick={onRetry}>Try again</Button>}
    >
      <p className={s.lead}>
        The service that runs your projects didn't reply. It usually comes back on its own within a minute, and
        this page keeps checking.
      </p>
    </StatusScreen>
  );
}
