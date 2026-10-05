import { useCallback, useEffect, useState } from "react";
import { Button } from "@eggie/ui";
import { type Account, type StartOutcome, signInError, signInLink, startError } from "../../account/account";
import { createApi, type SessionLoss, isSessionLost } from "../../api/client";
import { Qr } from "../../components/Qr";
import { useNow } from "../../projects/useNow";
import { SleepyEgg } from "../SleepyEgg";
import { StatusScreen } from "../StatusScreen";
import s from "../StatusScreen.module.css";
import own from "./SignIn.module.css";

const api = createApi((input, init) => fetch(input, init));
const POLL_MS = 3000;

// A "sessionLost" outcome is handed to onSessionLost and never stored here.
type ScreenOutcome = Exclude<StartOutcome, { kind: "sessionLost" }>;
type View = { account: Account | null; outcome: ScreenOutcome | null; starting: boolean };

export function SignIn({
  onSignedIn,
  onSessionLost,
}: {
  onSignedIn: () => void;
  onSessionLost: (reason: SessionLoss) => void;
}) {
  const [view, setView] = useState<View>({ account: null, outcome: null, starting: false });

  const refresh = useCallback(async () => {
    try {
      const account = await api.get<Account>("/api/account");
      if (account.state === "signed_in") onSignedIn();
      else setView((v) => ({ ...v, account }));
    } catch (error) {
      // A missed poll is retried on the next tick; a lost session is not.
      if (isSessionLost(error)) onSessionLost(error.code);
    }
  }, [onSignedIn, onSessionLost]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const start = async () => {
    setView((v) => ({ ...v, starting: true, outcome: null }));
    try {
      const account = await api.post<Account>("/api/account/sign-in");
      setView({ account, outcome: null, starting: false });
    } catch (error) {
      const outcome = startError(error);
      if (outcome.kind === "sessionLost") {
        onSessionLost(outcome.reason);
        return;
      }
      setView((v) => ({ ...v, outcome, starting: false }));
    }
  };

  const account = view.account;
  if (account?.state === "pending") return <Pending account={account} />;

  const reason = view.outcome?.message ?? signInError(account?.state === "signed_out" ? account.error : null);
  return (
    <StatusScreen
      art={<SleepyEgg />}
      title="Sign in to Eggie"
      actions={
        <Button variant="primary" size="lg" onClick={start} disabled={view.starting}>
          {view.outcome?.kind === "unreachable" ? "Try again" : "Sign in"}
        </Button>
      }
      footer="Your projects keep running while you sign in."
    >
      {reason && <p className={own.error}>{reason}</p>}
      <p className={s.lead}>Your Eggie account keeps track of your projects. Sign in once on this computer.</p>
    </StatusScreen>
  );
}

function Pending({ account }: { account: Extract<Account, { state: "pending" }> }) {
  const now = useNow(1000);
  const left = Math.max(0, Math.round(account.expires_at - now / 1000));
  const link = signInLink(account.url);
  if (link === null) {
    return (
      <StatusScreen art={<SleepyEgg />} title="Sign-in isn't available" actions={null}>
        <p className={s.lead}>The link the Eggie service sent isn't valid. This page keeps waiting for a good one.</p>
        <p className={s.detail}>{account.url}</p>
      </StatusScreen>
    );
  }
  return (
    <StatusScreen
      tone="yolk"
      art={
        <div className={own.qr}>
          <Qr value={link} />
        </div>
      }
      title="Approve this computer"
      actions={
        <a className={own.link} href={link} target="_blank" rel="noopener noreferrer">
          Open the sign-in page
        </a>
      }
      footer={left > 0 ? `This code works for ${Math.ceil(left / 60)} more minute${left > 60 ? "s" : ""}.` : "This code has run out."}
    >
      <p className={s.lead}>Scan the code with your phone, or open the sign-in page, and check it shows:</p>
      <p className={own.code}>{account.user_code}</p>
    </StatusScreen>
  );
}
