import { useState } from "react";
import { Button, Modal } from "@eggie/ui";
import { openExternal } from "../../desktop/desktop";
import { DEVICE_URL, useConnectGitHub, useGitHub, useReapplyGitHub } from "../../github/github";
import { githubView } from "../../github/view";
import { useNow } from "../../projects/useNow";
import { RepoPicker } from "./RepoPicker";
import s from "./GitHubModal.module.css";

export function GitHubModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const status = useGitHub();
  const connect = useConnectGitHub();
  const reapply = useReapplyGitHub();

  // Opened inside the click so no popup blocker trips; the URL never varies.
  const start = () => {
    openExternal(DEVICE_URL);
    connect.mutate();
  };

  let body;
  if (!status.data) {
    body = <p className={s.muted}>{status.isError ? status.error.message : "Checking GitHub…"}</p>;
  } else {
    const view = githubView(status.data);
    switch (view.kind) {
      case "connect":
        body = (
          <>
            {view.problem && <p className={s.error}>{view.problem}</p>}
            <p>Connect once and your coding agent can pull and push your GitHub repositories.</p>
            {connect.error && <p className={s.error}>{connect.error.message}</p>}
            <Button variant="primary" onClick={start} disabled={connect.isPending}>{view.action}</Button>
          </>
        );
        break;
      case "code":
        body = <Code code={view.code} expiresAt={view.expiresAt} />;
        break;
      case "applying":
        body = <p>Connected as @{view.login}. Setting up GitHub inside Eggie…</p>;
        break;
      case "ready":
        body = <RepoPicker login={view.login} onDone={onClose} />;
        break;
      case "setupFailed":
        body = (
          <>
            <p className={s.error}>{view.message}</p>
            {view.canRetry && (
              <Button onClick={() => reapply.mutate()} disabled={reapply.isPending}>Try again</Button>
            )}
          </>
        );
        break;
      case "reconnect":
        body = (
          <>
            <p>GitHub stopped accepting Eggie's access for @{view.login}.</p>
            <Button variant="primary" onClick={start} disabled={connect.isPending}>Reconnect GitHub</Button>
          </>
        );
        break;
    }
  }

  // Rendered only while open: with it always mounted (from ProjectList and
  // the account menu), an always-rendered RepoPicker would poll GitHub and
  // keep its clone state alive even with the dialog closed.
  return (
    <Modal open={open} onClose={onClose} title="GitHub">
      <div className={s.body}>{open && body}</div>
    </Modal>
  );
}

function Code({ code, expiresAt }: { code: string; expiresAt: number }) {
  const now = useNow(1000);
  const [copied, setCopied] = useState(false);
  const left = Math.max(0, Math.round(expiresAt - now / 1000));
  return (
    <>
      <p>On the GitHub page that just opened, enter this code and click Authorize:</p>
      <p className={s.code}>{code}</p>
      <div className={s.actions}>
        <Button
          variant="primary"
          onClick={() => void navigator.clipboard.writeText(code).then(() => setCopied(true))}
        >
          {copied ? "Copied" : "Copy code"}
        </Button>
        <Button variant="quiet" onClick={() => openExternal(DEVICE_URL)}>Open GitHub again</Button>
      </div>
      <p className={s.muted}>
        {left > 0 ? `This code works for ${Math.ceil(left / 60)} more minute${left > 60 ? "s" : ""}.` : "This code has run out."}
      </p>
    </>
  );
}
