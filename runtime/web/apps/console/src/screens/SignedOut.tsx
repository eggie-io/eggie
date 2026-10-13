import { useEffect } from "react";
import { Button } from "@eggie/ui";
import type { SignedOutReason } from "../boot/boot";
import { useSite } from "../site/SiteContext";
import { SleepyEgg } from "./SleepyEgg";
import { StatusScreen } from "./StatusScreen";
import s from "./StatusScreen.module.css";

export function SignedOut({ reason, onRetry }: { reason: SignedOutReason; onRetry: () => void }) {
  const site = useSite();
  useEffect(() => {
    // "Open Eggie" opens a new tab; this one catches up when it's looked at again.
    const onVisible = () => {
      if (document.visibilityState === "visible") onRetry();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [onRetry]);

  return (
    <StatusScreen
      art={<SleepyEgg />}
      title="We've lost track of you"
      actions={<Button variant="primary" size="lg" onClick={onRetry}>Try again</Button>}
      footer="Your projects carried on the whole time. Nothing stopped, nothing was lost."
    >
      <p className={s.lead}>
        {reason === "handoff_spent"
          ? "That sign-in link was already used, or it ran out — they only last a minute."
          : "This page can't tell who you are any more — that happens after a while."}
      </p>
      <p className={s.lead}>
        {site ? (
          <>Go back to the Eggie web app and open this console from there again.</>
        ) : (
          <>Open the Eggie app on your desktop and press <strong>Open Eggie</strong>. It'll hand the keys back.</>
        )}
      </p>
    </StatusScreen>
  );
}
