import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Modal, Notice } from "@eggie/ui";
import { actionLog, useProjectActions, useStartAction, useStopAction } from "../../actions/queries";
import { cardFor } from "../../actions/card";
import type { Action, Run } from "../../actions/types";
import { ApiError } from "../../api/client";
import { actionError } from "../../projects/copy";
import s from "./ActionCard.module.css";

function runStatus(run: Run | null): { tone: "success" | "neutral" | "danger"; text: string } {
  if (run?.state === "running") return { tone: "success", text: "Running" };
  if (run?.state === "exited") return { tone: run.exit_code === 0 ? "neutral" : "danger", text: `Exited ${run.exit_code ?? ""}`.trim() };
  return { tone: "neutral", text: "Stopped" };
}

function LogModal({ projectId, action, onClose }: { projectId: string; action: Action; onClose: () => void }) {
  const log = useQuery({ queryKey: ["action-log", projectId, action.id], queryFn: () => actionLog(projectId, action.id), gcTime: 0 });
  return (
    <Modal open onClose={onClose} title={`${action.name} log`} wide>
      {log.isError ? (
        <Notice tone="danger">{actionError(log.error)}</Notice>
      ) : log.data === undefined ? (
        <p className={s.muted}>Loading…</p>
      ) : (
        <pre className={s.log}>{log.data === "" ? "Nothing printed yet." : log.data}</pre>
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
  const [logFor, setLogFor] = useState<Action | null>(null);
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
            <Button variant="ghost" onClick={() => setLogFor(action)}>Logs</Button>
          </>
        ) : running ? (
          <Button icon="terminal" onClick={() => onOpenTerminal(action)}>Show output</Button>
        ) : (
          <>
            <Button variant="primary" icon="play" onClick={begin} disabled={start.isPending}>{action.run ? "Run again" : "Run"}</Button>
            {action.run && <Button icon="terminal" onClick={() => onOpenTerminal(action)}>Show output</Button>}
          </>
        )}
      </div>
      {logFor && <LogModal projectId={projectId} action={logFor} onClose={() => setLogFor(null)} />}
    </div>
  );
}
