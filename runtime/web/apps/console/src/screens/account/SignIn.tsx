import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@eggie/ui";
import {
  type Account, type Intent, type SignInView, type StartOutcome,
  liveLink, signInError, signInView, startError,
} from "../../account/account";
import { createApi, type SessionLoss, isSessionLost } from "../../api/client";
import { Qr } from "../../components/Qr";
import { openExternal } from "../../desktop/desktop";
import { useNow } from "../../projects/useNow";
import { Shell } from "../../shell/Shell";
import { webAppInstances } from "../../site/signIn";
import { useSite, useWebApp } from "../../site/SiteContext";
import { SleepyEgg } from "../SleepyEgg";
import { StatusScreen } from "../StatusScreen";
import s from "../StatusScreen.module.css";
import own from "./SignIn.module.css";

const api = createApi((input, init) => fetch(input, init));
const POLL_MS = 3000;

// A "sessionLost" outcome is handed to onSessionLost and never stored here.
type ScreenOutcome = Exclude<StartOutcome, { kind: "sessionLost" }>;
type View = { account: Account | null; intent: Intent | null; outcome: ScreenOutcome | null; starting: boolean };

function hostOf(url: string | null | undefined): string | null {
  try {
    return url ? new URL(url).host : null;
  } catch {
    return null;
  }
}

export function SignIn({
  onSignedIn,
  onSessionLost,
}: {
  onSignedIn: () => void;
  onSessionLost: (reason: SessionLoss) => void;
}) {
  const site = useSite();
  const webApp = useWebApp();
  const managed = typeof site === "string";
  const [view, setView] = useState<View>({ account: null, intent: null, outcome: null, starting: false });
  const latest = useRef(view);
  latest.current = view;
  const asking = useRef<Promise<Account> | null>(null);
  // Bumped around every code request, so a poll that overlapped one is dropped
  // instead of putting back the account it saw before the code existed.
  const asked = useRef(0);

  const refresh = useCallback(async () => {
    const before = asked.current;
    try {
      const account = await api.get<Account>("/api/account");
      if (asked.current !== before) return;
      if (account.state === "signed_in") return onSignedIn();
      setView((v) => {
        if (account.state === "pending" || v.intent === null) return { ...v, account };
        // The code they were waiting on is gone: back to the choice, saying why.
        const message = signInError(account.error);
        return { ...v, account, intent: null, outcome: message ? { kind: "error", message } : null };
      });
    } catch (error) {
      // A missed poll is retried on the next tick; a lost session is not.
      if (isSessionLost(error)) onSessionLost(error.code);
    }
  }, [onSignedIn, onSessionLost]);

  const ask = useCallback(() => {
    if (asking.current === null) {
      asked.current += 1;
      asking.current = api.post<Account>("/api/account/sign-in").finally(() => {
        asked.current += 1;
        asking.current = null;
      });
    }
    return asking.current;
  }, []);

  // WKWebView refuses a new window opened after an await, so the code is
  // asked for ahead of the press and the press only opens its link.
  const prepare = useCallback(() => {
    if (managed || liveLink(latest.current.account, Date.now()) !== null) return;
    ask().then(
      (account) => (account.state === "signed_in" ? onSignedIn() : setView((v) => ({ ...v, account }))),
      () => {},
    );
  }, [managed, ask, onSignedIn]);

  useEffect(() => {
    void refresh();
    prepare();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh, prepare]);

  const open = (account: Account, intent: Intent) => {
    const next = signInView(account, intent);
    if (next.kind === "finish") openExternal(next.target);
  };

  const choose = async (intent: Intent) => {
    const ready = latest.current.account;
    if (ready !== null && liveLink(ready, Date.now()) !== null) {
      open(ready, intent);
      setView((v) => ({ ...v, intent, outcome: null }));
      return;
    }
    setView((v) => ({ ...v, starting: true, outcome: null }));
    try {
      const account = await ask();
      if (account.state === "signed_in") return onSignedIn();
      // Opens where the press outlives the request; "Open the page again" covers the rest.
      open(account, intent);
      setView({ account, intent, outcome: null, starting: false });
    } catch (error) {
      const outcome = startError(error);
      if (outcome.kind === "sessionLost") {
        onSessionLost(outcome.reason);
        return;
      }
      setView((v) => ({ ...v, outcome, starting: false }));
    }
  };

  const instancesUrl = webAppInstances(webApp);

  // A site refuses sign-in from the console, so the button would only dead-end.
  if (managed) {
    return (
      <StatusScreen
        art={<SleepyEgg />}
        title="This server lost its Eggie sign-in"
        actions={
          instancesUrl !== null && (
            <a className={own.link} href={instancesUrl}>Open the Eggie web app</a>
          )
        }
      >
        <p className={s.lead}>It can't sign in again from here. Set it up again from the Eggie web app.</p>
      </StatusScreen>
    );
  }

  const shown = signInView(view.account, view.intent);
  if (shown.kind === "finish") {
    return <Finish view={shown} onBack={() => setView((v) => ({ ...v, intent: null, outcome: null }))} />;
  }
  if (shown.kind === "badLink") {
    return (
      <StatusScreen art={<SleepyEgg />} title="Sign-in isn't available" actions={null}>
        <p className={s.lead}>The link the Eggie service sent isn't valid. This page keeps waiting for a good one.</p>
        <p className={s.detail}>{shown.url}</p>
      </StatusScreen>
    );
  }

  const reason = view.outcome?.message ?? shown.reason;
  const ahead = { onPointerEnter: prepare, onPointerDown: prepare, onFocus: prepare };
  return (
    <StatusScreen
      art={<SleepyEgg />}
      title="Create your free Eggie account"
      actions={
        <>
          <Button variant="primary" size="lg" iconRight="external" disabled={view.starting} onClick={() => void choose("new")} {...ahead}>
            Create free account
          </Button>
          <Button variant="secondary" size="lg" disabled={view.starting} onClick={() => void choose("existing")} {...ahead}>
            I already have an account
          </Button>
        </>
      }
      footer={`Both open ${hostOf(webApp) ?? "the Eggie web app"} in your browser. This window waits for you.`}
    >
      {reason && <p className={own.error}>{reason}</p>}
      <p className={s.lead}>
        An Eggie account keeps track of your projects. It’s free, takes about a minute, and you only set it up once on
        this computer.
      </p>
    </StatusScreen>
  );
}

function Finish({ view, onBack }: { view: Extract<SignInView, { kind: "finish" }>; onBack: () => void }) {
  const now = useNow(1000);
  const left = Math.max(0, Math.round(view.expiresAt - now / 1000));
  return (
    <Shell tone="yolk">
      <div className={own.finish}>
        <div className={own.main}>
          <div className={own.head}>
            <p className={own.waiting}><span className={own.spinner} aria-hidden="true" />Waiting for you in the browser</p>
            <h1 className={own.title}>Finish in your browser</h1>
            <p className={own.lead}>
              We opened <span className={own.host}>{view.host}</span> for you. Do these three things there. This window
              carries on by itself when you’re done.
            </p>
          </div>
          <ol className={own.steps}>
            {view.intent === "new" ? (
              <Step n={1} title="Create your account" hint="With your email and a password. It’s free." />
            ) : (
              <Step n={1} title="Sign in" hint="Use the account you already have." />
            )}
            <Step n={2} title="Check the page shows this code" hint="If the codes match, it’s really this computer asking.">
              <span className={own.code}>{view.code}</span>
            </Step>
            <Step n={3} title="Press Approve" hint="The page asks “Approve this device?”. After that, Eggie opens here by itself." />
          </ol>
          <div className={own.row}>
            <Button variant="secondary" iconRight="external" onClick={() => openExternal(view.target)}>
              Open the page again
            </Button>
            <Button variant="ghost" icon="chevron-left" onClick={onBack}>Back</Button>
            <span className={own.left}>
              {left > 0 ? `This code works for ${Math.ceil(left / 60)} more minute${left > 60 ? "s" : ""}.` : "This code has run out."}
            </span>
          </div>
        </div>
        <aside className={own.phone}>
          <div className={own.qr}>
            <Qr value={view.target} label="QR code that opens the same page on a phone" size={168} />
          </div>
          <div className={own.phoneText}>
            <span className={own.phoneTitle}>Or use your phone</span>
            <span className={own.hint}>Scan the code and do the same three steps there.</span>
          </div>
        </aside>
      </div>
    </Shell>
  );
}

function Step({ n, title, hint, children }: { n: number; title: string; hint: string; children?: React.ReactNode }) {
  return (
    <li className={own.step}>
      <span className={own.number} aria-hidden="true">{n}</span>
      <div className={own.stepBody}>
        <div className={own.words}>
          <span className={own.stepTitle}>{title}</span>
          <span className={own.hint}>{hint}</span>
        </div>
        {children}
      </div>
    </li>
  );
}
