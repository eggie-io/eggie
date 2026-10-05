import { Button, Egg } from "@eggie/ui";
import { StatusScreen } from "./StatusScreen";
import s from "./StatusScreen.module.css";

export function WrongHost({ onRetry }: { onRetry: () => void }) {
  return (
    <StatusScreen
      art={<Egg tone="cold" size={96} />}
      title="This page was opened from an address Eggie doesn't recognise"
      actions={<Button variant="primary" size="lg" onClick={onRetry}>Try again</Button>}
    >
      <p className={s.lead}>
        Open it as <strong>http://localhost:39080</strong> — the desktop app's <strong>Open Eggie</strong> button
        does that for you.
      </p>
    </StatusScreen>
  );
}
