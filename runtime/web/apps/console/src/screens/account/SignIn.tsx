import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@eggie/ui";
import {
  type Account, type Intent, type SignInView, type Waiting,
  afterPoll, afterPress, afterQuietCode, liveLink, signInView, startError,
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

type View = Waiting & { starting: boolean };

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
  const [view, setView] = useState<View>({ account: null, intent: null, notice: null, opened: false, starting: false });
  // The ref is what the handlers read: a press must see the answer that
  // landed a moment ago, before the next render.
  const latest = useRef(view);
  const update = useCallback((next: (v: View) => View) => {
    latest.current = next(latest.current);
    setView(latest.current);
  }, []);
  const asking = useRef<Promise<Account> | null>(null);
  // Bumped around every code request, so a poll that overlapped one is dropped
  // instead of putting back the account it saw before the code existed.
  const asked = useRef(0);
  const polled = useRef(false);

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
  // asked for ahead of the press and the press only opens its link. Not
  // before the first poll: asking makes the API forget why it signed out.
  const prepare = useCallback(() => {
    if (managed || !polled.current || liveLink(latest.current.account) !== null) return;
    ask().then(
      (account) => (account.state === "signed_in" ? onSignedIn() : update((v) => afterQuietCode(v, account))),
      () => {},
    );
  }, [managed, ask, onSignedIn, update]);

  const refresh = useCallback(async () => {
    const before = asked.current;
    try {
      const account = await api.get<Account>("/api/account");
      if (asked.current !== before) return;
      if (account.state === "signed_in") return onSignedIn();
      update((v) => afterPoll(v, account));
      if (!polled.current) {
        polled.current = true;
        prepare();
      }
    } catch (error) {
      // A missed poll is retried on the next tick; a lost session is not.
      if (isSessionLost(error)) onSessionLost(error.code);
    }
  }, [onSignedIn, onSessionLost, update, prepare]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const choose = async (intent: Intent) => {
    const { next, open } = afterPress(latest.current, intent);
    if (open !== null) {
      openExternal(open);
      update(() => next);
      return;
    }
    update((v) => ({ ...v, starting: true, notice: null }));
    try {
      const account = await ask();
      if (account.state === "signed_in") return onSignedIn();
      // Too late to open a window for this press: the waiting view offers the page instead.
      update((v) => ({ ...v, account, intent, opened: false, notice: null, starting: false }));
    } catch (error) {
      const outcome = startError(error);
      if (outcome.kind === "sessionLost") {
        onSessionLost(outcome.reason);
        return;
      }
      update((v) => ({ ...v, notice: outcome.message, starting: false }));
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
    return (
      <Finish
        view={shown}
        opened={view.opened}
        onOpened={() => update((v) => ({ ...v, opened: true }))}
        onBack={() => update((v) => ({ ...v, intent: null, notice: null }))}
      />
    );
  }
  if (shown.kind === "badLink") {
    return (
      <StatusScreen art={<SleepyEgg />} title="Sign-in isn't available" actions={null}>
        <p className={s.lead}>The link the Eggie service sent isn't valid. This page keeps waiting for a good one.</p>
        <p className={s.detail}>{shown.url}</p>
      </StatusScreen>
    );
  }

  const reason = view.notice ?? shown.reason;
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
      {reason && <p className={own.error} role="alert">{reason}</p>}
      <p className={s.lead}>
        An Eggie account keeps track of your projects. It’s free, takes about a minute, and you only set it up once on
        this computer.
      </p>
    </StatusScreen>
  );
}

function Finish({
  view,
  opened,
  onOpened,
  onBack,
}: {
  view: Extract<SignInView, { kind: "finish" }>;
  opened: boolean;
  onOpened: () => void;
  onBack: () => void;
}) {
  const now = useNow(1000);
  const left = Math.max(0, Math.round(view.expiresAt - now / 1000));
  const host = <span className={own.host}>{view.host}</span>;
  const openPage = () => {
    openExternal(view.target);
    onOpened();
  };
  return (
    <Shell tone="yolk">
      <div className={own.finish}>
        <div className={own.main}>
          <div className={own.head}>
            <p className={own.waiting} role="status"><span className={own.spinner} aria-hidden="true" />Waiting for you in the browser</p>
            <h1 className={own.title}>Finish in your browser</h1>
            <p className={own.lead}>
              {opened ? <>We opened {host} for you. Do these three things there.</> : <>Open {host} and do these three things there.</>}
              {" "}This window carries on by itself when you’re done.
            </p>
          </div>
          <ol className={own.steps} role="list">
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
            {opened ? (
              <Button variant="secondary" iconRight="external" onClick={openPage}>Open the page again</Button>
            ) : (
              <Button variant="primary" iconRight="external" onClick={openPage}>Open {view.host}</Button>
            )}
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
