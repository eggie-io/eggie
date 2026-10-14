import { Button, Egg } from "@eggie/ui";
import { StatusScreen } from "./StatusScreen";
import { isLocalHost } from "../site/SiteContext";
import s from "./StatusScreen.module.css";

export function WrongHost({ onRetry }: { onRetry: () => void }) {
  // The API refused this host, so /api/health can't say whether this is a site;
  // the page's own address can.
  const local = isLocalHost(window.location.hostname);
  return (
    <StatusScreen
      art={<Egg tone="cold" size={96} />}
      title="This page was opened from an address Eggie doesn't recognise"
      actions={<Button variant="primary" size="lg" onClick={onRetry}>Try again</Button>}
    >
      {local ? (
        <p className={s.lead}>
          Open it as <strong>http://localhost:39080</strong> — the desktop app's <strong>Open Eggie</strong> button
          does that for you.
        </p>
      ) : (
        <p className={s.lead}>
          This console is reached from the Eggie web app. Go back there and open it again.
        </p>
      )}
    </StatusScreen>
  );
}
