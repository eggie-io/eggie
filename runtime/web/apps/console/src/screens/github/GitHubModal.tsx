import { useState, type ReactNode } from "react";
import { Button, Modal, Notice } from "@eggie/ui";
import { openExternal } from "../../desktop/desktop";
import { DEVICE_URL, useConnectGitHub, useGitHub, useReapplyGitHub } from "../../github/github";
import { githubView } from "../../github/view";
import { useJob } from "../../projects/queries";
import { useNow } from "../../projects/useNow";
import { Cloning, RepoPicker, type Started } from "./RepoPicker";
import s from "./GitHubModal.module.css";

export function GitHubModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const status = useGitHub();
  const connect = useConnectGitHub();
  const reapply = useReapplyGitHub();
  // Held here rather than in RepoPicker so a failed clone's "Pick another" can sit in the footer.
  const [started, setStarted] = useState<Started | null>(null);
  const job = useJob(started?.job_id ?? null).data;

  // Opened inside the click so no popup blocker trips; the URL never varies.
  const start = () => {
    openExternal(DEVICE_URL);
    connect.mutate();
  };

  const view = status.data ? githubView(status.data) : null;
  // A clone is forgotten with the dialog or the connection it ran under.
  if (started && (!open || view?.kind !== "ready")) setStarted(null);

  let body: ReactNode;
  let footer: ReactNode;
  if (!view) {
    body = <p>{status.isError ? status.error.message : "Checking GitHub…"}</p>;
  } else {
    switch (view.kind) {
      case "connect":
        body = (
          <>
            {view.problem && <Notice tone="danger">{view.problem}</Notice>}
            <p>Connect once and your coding agent can pull and push your GitHub repositories.</p>
            {connect.error && <Notice tone="danger">{connect.error.message}</Notice>}
          </>
        );
        footer = <Button variant="primary" icon="branch" onClick={start} disabled={connect.isPending}>{view.action}</Button>;
        break;
      case "code":
        body = <Code code={view.code} expiresAt={view.expiresAt} />;
        footer = (
          <>
            <Button variant="ghost" iconRight="external" onClick={() => openExternal(DEVICE_URL)}>Open GitHub again</Button>
            <CopyCode key={view.code} code={view.code} />
          </>
        );
        break;
      case "applying":
        body = <p>Connected as @{view.login}. Setting up GitHub inside Eggie…</p>;
        break;
      case "ready":
        if (started) {
          body = <Cloning key={started.job_id} id={started.id} job={job} onDone={onClose} />;
          if (job?.state === "failed") footer = <Button onClick={() => setStarted(null)}>Pick another</Button>;
        } else {
          body = <RepoPicker login={view.login} onDone={onClose} onStarted={setStarted} />;
        }
        break;
      case "setupFailed":
        body = <Notice tone="danger">{view.message}</Notice>;
        if (view.canRetry) {
          footer = <Button icon="refresh" onClick={() => reapply.mutate()} disabled={reapply.isPending}>Try again</Button>;
        }
        break;
      case "reconnect":
        body = <p>GitHub stopped accepting Eggie's access for @{view.login}.</p>;
        footer = <Button variant="primary" icon="branch" onClick={start} disabled={connect.isPending}>Reconnect GitHub</Button>;
        break;
    }
  }

  // Rendered only while open: with it always mounted (from ProjectList and
  // the account menu), an always-rendered RepoPicker would poll GitHub and
  // keep its clone state alive even with the dialog closed.
  return (
    <Modal open={open} onClose={onClose} title="GitHub" footer={open ? footer : undefined}>
      <div className={s.body}>{open && body}</div>
    </Modal>
  );
}

function Code({ code, expiresAt }: { code: string; expiresAt: number }) {
  const now = useNow(1000);
  const left = Math.max(0, Math.round(expiresAt - now / 1000));
  return (
    <>
      <p>On the GitHub page that just opened, enter this code and click Authorize:</p>
      <p className={s.code}>{code}</p>
      <p>
        {left > 0 ? `This code works for ${Math.ceil(left / 60)} more minute${left > 60 ? "s" : ""}.` : "This code has run out."}
      </p>
    </>
  );
}

function CopyCode({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="primary"
      icon={copied ? "check" : "copy"}
      onClick={() => void navigator.clipboard.writeText(code).then(() => setCopied(true))}
    >
      {copied ? "Copied" : "Copy code"}
    </Button>
  );
}
