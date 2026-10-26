import { useEffect } from "react";
import { Button } from "@eggie/ui";
import type { SignedOutReason } from "../boot/boot";
import { openInstanceUrl, siteSignedOutAction } from "../site/signIn";
import { useSite, useWebApp } from "../site/SiteContext";
import { SleepyEgg } from "./SleepyEgg";
import { StatusScreen } from "./StatusScreen";
import s from "./StatusScreen.module.css";

export function SignedOut({ reason, onRetry }: { reason: SignedOutReason; onRetry: () => void }) {
  const site = useSite();
  const webApp = useWebApp();
  const link =
    typeof site === "string" && typeof webApp === "string" ? openInstanceUrl(webApp, site) : null;
  const redirecting = link !== null && siteSignedOutAction(reason) === "redirect";

  useEffect(() => {
    if (redirecting) window.location.replace(link);
  }, [redirecting, link]);

  useEffect(() => {
    // "Open Eggie" opens a new tab; this one catches up when it's looked at again.
    // Not on a site: a retry there boots without a code and would redirect past the Sign in button.
    if (typeof site === "string") return;
    const onVisible = () => {
      if (document.visibilityState === "visible") onRetry();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [onRetry, site]);

  if (redirecting) {
    return (
      <StatusScreen art={<SleepyEgg />} title="Signing you in" actions={null}>
        <p className={s.lead}>Taking you to the Eggie web app to sign in…</p>
      </StatusScreen>
    );
  }

  return (
    <StatusScreen
      art={<SleepyEgg />}
      title="We've lost track of you"
      actions={
        link !== null ? (
          <>
            <Button variant="primary" size="lg" onClick={() => window.location.assign(link)}>Sign in</Button>
            <Button variant="secondary" size="lg" onClick={onRetry}>Try again</Button>
          </>
        ) : (
          <Button variant="primary" size="lg" onClick={onRetry}>Try again</Button>
        )
      }
      footer="Your projects carried on the whole time. Nothing stopped, nothing was lost."
    >
      <p className={s.lead}>
        {reason === "handoff_spent"
          ? "That sign-in link was already used, or it ran out — they only last a minute."
          : "This page can't tell who you are any more — that happens after a while."}
      </p>
      <p className={s.lead}>
        {site ? (
          <>Sign in again through the Eggie web app.</>
        ) : (
          <>Open the Eggie app on your desktop and press <strong>Open Eggie</strong>. It'll hand the keys back.</>
        )}
      </p>
    </StatusScreen>
  );
}
