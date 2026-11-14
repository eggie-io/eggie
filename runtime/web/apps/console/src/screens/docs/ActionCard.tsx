import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Modal, Notice } from "@eggie/ui";
import { actionLog, actionOutput, useProjectActions, useStartAction, useStopAction } from "../../actions/queries";
import { stripAnsi } from "../../actions/ansi";
import { cardFor, runStatus } from "../../actions/card";
import type { Action } from "../../actions/types";
import { ApiError } from "../../api/client";
import { actionError } from "../../projects/copy";
import s from "./ActionCard.module.css";

function TextModal({
  title,
  queryKey,
  fetch,
  onClose,
}: {
  title: string;
  queryKey: readonly string[];
  fetch: () => Promise<string>;
  onClose: () => void;
}) {
  const text = useQuery({ queryKey, queryFn: fetch, gcTime: 0 });
  const pre = useRef<HTMLPreElement>(null);
  const scrolled = useRef(false);
  // Only the first load: a refetch must not move the owner off what they are reading.
  useEffect(() => {
    if (scrolled.current || !pre.current) return;
    pre.current.scrollTop = pre.current.scrollHeight;
    scrolled.current = true;
  }, [text.data]);
  return (
    <Modal open onClose={onClose} title={title} wide>
      {text.isError ? (
        <Notice tone="danger">{actionError(text.error)}</Notice>
      ) : text.data === undefined ? (
        <p className={s.muted}>Loading…</p>
      ) : (
        <pre ref={pre} className={s.log}>{stripAnsi(text.data) || "Nothing printed yet."}</pre>
      )}
    </Modal>
  );
}

export function ActionCard({
  projectId,
  id,
  onOpenTerminal,
}: {
  projectId: string;
  id: string;
  onOpenTerminal: (action: Action) => void;
}) {
  const actions = useProjectActions(projectId);
  const start = useStartAction(projectId);
  const stop = useStopAction(projectId);
  const [view, setView] = useState<"log" | "output" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const card = cardFor(actions.data, actions.error, id);

  if (card.kind === "loading") return <div className={s.card}><p className={s.muted}>Loading…</p></div>;
  if (card.kind === "unknown") return <div className={s.card}><p className={s.muted}>This project has no action <code>{card.id}</code></p></div>;
  if (card.kind === "unavailable") return <div className={s.card}><p className={s.muted}>{card.message}</p></div>;

  const action = card.action;
  const running = action.run?.state === "running";
  const longRunning = action.type === "long-running";
  const status = runStatus(action.run);

  const begin = () => {
    setProblem(null);
    start.mutate(action.id, {
      onSuccess: () => onOpenTerminal(action),
      onError: (error) => {
        if (error instanceof ApiError && error.code === "action_running") onOpenTerminal(action);
        else setProblem(actionError(error));
      },
    });
  };
  const end = () => {
    setProblem(null);
    stop.mutate(action.id, { onError: (error) => setProblem(actionError(error)) });
  };

  return (
    <div className={s.card}>
      <div className={s.head}>
        <div className={s.name}>{action.name}</div>
        {(longRunning || action.run) && <Badge tone={status.tone}>{status.text}</Badge>}
      </div>
      {action.description && <p className={s.description}>{action.description}</p>}
      <pre className={s.command}>{action.command}</pre>
      {problem && <Notice tone="danger">{problem}</Notice>}
      <div className={s.buttons}>
        {longRunning ? (
          <>
            {running ? (
              <Button icon="stop" onClick={end} disabled={stop.isPending}>Stop</Button>
            ) : (
              <Button variant="primary" icon="play" onClick={begin} disabled={start.isPending}>Start</Button>
            )}
            <Button icon="terminal" onClick={() => onOpenTerminal(action)}>Open terminal</Button>
            <Button variant="ghost" onClick={() => setView("log")}>Logs</Button>
          </>
        ) : (
          <>
            {!running && <Button variant="primary" icon="play" onClick={begin} disabled={start.isPending}>{action.run ? "Run again" : "Run"}</Button>}
            {action.run && <Button icon="terminal" onClick={() => onOpenTerminal(action)}>Open terminal</Button>}
            {action.run && <Button variant="ghost" onClick={() => setView("output")}>Full output</Button>}
          </>
        )}
      </div>
      {view === "log" && (
        <TextModal title={`${action.name} log`} queryKey={["action-log", projectId, action.id]}
          fetch={() => actionLog(projectId, action.id)} onClose={() => setView(null)} />
      )}
      {view === "output" && (
        <TextModal title={`${action.name} — full output`} queryKey={["action-output", projectId, action.id]}
          fetch={() => actionOutput(projectId, action.id)} onClose={() => setView(null)} />
      )}
    </div>
  );
}
