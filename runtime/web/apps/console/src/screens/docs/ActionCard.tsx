import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Modal, Notice } from "@eggie/ui";
import {
  actionLog,
  actionOutput,
  useActionRun,
  useAnswerAction,
  useConnectionStates,
  useProjectActions,
  useStartAction,
  useStopAction,
} from "../../actions/queries";
import { stripAnsi } from "../../actions/ansi";
import { cardFor, gate, runStatus } from "../../actions/card";
import type { TerminalTarget } from "../../actions/types";
import { ApiError } from "../../api/client";
import { actionError } from "../../projects/copy";
import { ConnectionRow } from "./ConnectionRow";
import { QuestionField } from "./QuestionField";
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
  onOpenTerminal: (target: TerminalTarget) => void;
}) {
  const actions = useProjectActions(projectId);
  const start = useStartAction(projectId);
  const stop = useStopAction(projectId);
  const [view, setView] = useState<"log" | "output" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const card = cardFor(actions.data, actions.error, id);
  const found = card.kind === "action" ? card.action : null;
  const info = actions.data?.connections ?? [];
  const states = useConnectionStates(
    (found?.connections ?? []).filter((cid) => info.some((c) => c.id === cid && c.login)),
  );
  const asks = found?.asks ?? false;
  const running = found?.run?.state === "running";
  const live = useActionRun(projectId, id, asks && running);
  const answer = useAnswerAction(projectId, id);
  const [answerProblem, setAnswerProblem] = useState<{ key: string; message: string } | null>(null);

  if (card.kind === "loading") return <div className={s.card}><p className={s.muted}>Loading…</p></div>;
  if (card.kind === "unknown") return <div className={s.card}><p className={s.muted}>This project has no action <code>{card.id}</code></p></div>;
  if (card.kind === "unavailable") return <div className={s.card}><p className={s.muted}>{card.message}</p></div>;

  const action = card.action;
  const longRunning = action.type === "long-running";
  const status = runStatus(action.run);
  const blocked = gate(action, info, states);
  // A previous run's last poll stays cached; only this run's question counts.
  const asking = running && live.data?.run?.state === "running" && live.data.run.started_at === action.run?.started_at
    ? live.data.asking
    : null;
  const askingKey = asking ? `${asking.source}:${asking.id}` : "";

  // With questions the card answers them, so the terminal stays closed unless asked for.
  const openTerminal = () => { if (!asks) onOpenTerminal({ kind: "action", action }); };
  const begin = () => {
    setProblem(null);
    start.mutate(action.id, {
      onSuccess: openTerminal,
      onError: (error) => {
        if (error instanceof ApiError && error.code === "action_running") openTerminal();
        else setProblem(actionError(error));
      },
    });
  };
  const send = (value: string) => {
    if (!asking) return;
    const key = askingKey;
    setAnswerProblem(null);
    answer.mutate(
      { source: asking.source, question: asking.id, value },
      { onError: (error) => setAnswerProblem({ key, message: actionError(error) }) },
    );
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
      {action.connections.length > 0 && (
        <div className={s.connections}>
          {action.connections.map((cid) => {
            const connection = info.find((c) => c.id === cid);
            return connection && <ConnectionRow key={cid} connection={connection} onOpenTerminal={onOpenTerminal} />;
          })}
        </div>
      )}
      {problem && <Notice tone="danger">{problem}</Notice>}
      <div className={s.buttons}>
        {longRunning ? (
          <>
            {running ? (
              <Button icon="stop" onClick={end} disabled={stop.isPending}>Stop</Button>
            ) : (
              <Button variant="primary" icon="play" onClick={begin} disabled={start.isPending || blocked !== null}>Start</Button>
            )}
            <Button icon="terminal" onClick={() => onOpenTerminal({ kind: "action", action })}>Open terminal</Button>
            <Button variant="ghost" onClick={() => setView("log")}>Logs</Button>
          </>
        ) : running && asks ? (
          <>
            <span className={s.status}>Running…</span>
            <Button icon="terminal" onClick={() => onOpenTerminal({ kind: "action", action })}>Show terminal</Button>
            <Button icon="stop" onClick={end} disabled={stop.isPending}>Stop</Button>
          </>
        ) : (
          <>
            {!running && (
              <Button variant="primary" icon="play" onClick={begin} disabled={start.isPending || blocked !== null}>
                {action.run ? "Run again" : "Run"}
              </Button>
            )}
            {action.run && <Button icon="terminal" onClick={() => onOpenTerminal({ kind: "action", action })}>Open terminal</Button>}
            {action.run && <Button variant="ghost" onClick={() => setView("output")}>Full output</Button>}
          </>
        )}
        {!running && blocked && <span className={s.status}>{blocked}</span>}
      </div>
      {asking && (
        <QuestionField
          key={askingKey}
          asking={asking}
          onSend={send}
          sending={answer.isPending}
          error={answerProblem?.key === askingKey ? answerProblem.message : null}
        />
      )}
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
