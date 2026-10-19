import { useEffect, useRef, useState } from "react";
import { Button, Collapsible, Modal, Notice, PromptCard, cx } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { CAUSE_COPY, actionError, primaryUrl } from "../../projects/copy";
import { hostOf } from "../../projects/format";
import { fixPrompt } from "../../projects/prompts";
import { useLifecycle, useLogs } from "../../projects/queries";
import type { Project } from "../../projects/types";
import type { Cause } from "../../projects/view";
import { TitleRow } from "./TitleRow";
import s from "./ProjectPage.module.css";

function Logs({ id, wanted }: { id: string; wanted: boolean }) {
  const logs = useLogs(id, wanted);
  const pre = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (pre.current) pre.current.scrollTop = pre.current.scrollHeight;
  }, [logs.data]);

  if (logs.data !== undefined) {
    return <pre ref={pre} className={cx(s.code, s.logs)}>{logs.data.trim() ? logs.data : "There aren't any logs to show yet."}</pre>;
  }
  if (logs.isError) {
    return (
      <p className={s.muted}>
        {logs.error instanceof ApiError && logs.error.code === "logs_unavailable"
          ? "There aren't any logs to show yet."
          : logs.error.message}
      </p>
    );
  }
  return <p className={s.muted}>Fetching the logs…</p>;
}

export function WrongBody({ project, cause, detail }: { project: Project; cause: Cause; detail: string | null }) {
  const lifecycle = useLifecycle(project.id);
  const [fixing, setFixing] = useState(false);
  const [wantLogs, setWantLogs] = useState(false);
  const copy = CAUSE_COPY[cause];
  const primary = primaryUrl(project);

  // Clear a stale error notice from a previous action once the diagnosed
  // cause changes (still "wrong", but a different problem now).
  useEffect(() => {
    lifecycle.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cause]);

  return (
    <>
      <TitleRow title={project.id} sub="Something’s wrong" />
      <Notice tone="danger" title={copy.heading(project.id)}>{copy.body}</Notice>
      {detail && <pre className={s.code}>{detail}</pre>}
      <div className={s.row}>
        <Button variant="primary" icon="refresh" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart")}>Try again</Button>
        <Button onClick={() => setFixing(true)}>Get a prompt that fixes it</Button>
        <span className={s.note}>Nothing is lost. Your files are exactly where you left them.</span>
      </div>
      {lifecycle.error && <Notice tone="danger">{actionError(lifecycle.error)}</Notice>}
      <Collapsible
        boxed
        summary="The raw details"
        aside="for your coding agent, or for us"
        onToggle={(open) => open && setWantLogs(true)}
      >
        {wantLogs && <Logs id={project.id} wanted={wantLogs} />}
      </Collapsible>
      {primary && (
        <div className={s.was}>
          <span>Was going to be</span>
          <code>{hostOf(primary)}</code>
        </div>
      )}
      <Modal
        open={fixing}
        onClose={() => setFixing(false)}
        title="A prompt that fixes it"
        wide
        footer={<Button onClick={() => setFixing(false)}>Close</Button>}
      >
        <PromptCard prompt={fixPrompt(cause, project.id, detail)} />
      </Modal>
    </>
  );
}
