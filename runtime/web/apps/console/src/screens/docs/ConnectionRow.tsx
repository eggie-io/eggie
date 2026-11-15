import { useState, type ReactNode } from "react";
import { Button, Icon } from "@eggie/ui";
import { connectionView, type ConnectionView } from "../../actions/card";
import { useAnswerConnection, useConnection, useConnectionCancel, useConnectionLogin } from "../../actions/queries";
import type { ConnectionInfo, TerminalTarget } from "../../actions/types";
import { ApiError } from "../../api/client";
import { actionError } from "../../projects/copy";
import { QuestionField } from "./QuestionField";
import s from "./ActionCard.module.css";

type Props = { connection: ConnectionInfo; onOpenTerminal: (target: TerminalTarget) => void };

function Row({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className={s.connection}>
      <span className={s.connectionName}>{name}</span>
      <div className={s.connectionBody}>{children}</div>
    </div>
  );
}

function LoginRow({ connection, onOpenTerminal }: Props) {
  const answer = useConnection(connection.id);
  const login = useConnectionLogin(connection.id);
  const cancel = useConnectionCancel(connection.id);
  const reply = useAnswerConnection(connection.id);
  // The run this card started, by start time, so an older login's failure isn't reported as ours.
  const [mine, setMine] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [replyProblem, setReplyProblem] = useState<string | null>(null);

  const startedHere = mine !== null && answer.data?.run?.started_at === mine;
  const view: ConnectionView = login.isPending
    ? { kind: "getting-link" }
    : answer.data === undefined && answer.isError
      ? { kind: "unknown" }
      : connectionView(answer.data, startedHere);
  const { name } = connection;

  const begin = () => {
    setProblem(null);
    login.mutate(undefined, {
      onSuccess: (result) => setMine(result.run?.started_at ?? null),
      onError: (error) => {
        if (!(error instanceof ApiError && error.code === "connection_running")) setProblem(actionError(error));
      },
    });
  };
  const end = () => {
    setProblem(null);
    cancel.mutate(undefined, { onError: (error) => setProblem(actionError(error)) });
  };
  const send = (value: string) => {
    if (view.kind !== "asking") return;
    setReplyProblem(null);
    reply.mutate(
      { source: view.asking.source, question: view.asking.id, value },
      { onError: (error) => setReplyProblem(actionError(error)) },
    );
  };

  const showTerminal = (
    <Button size="sm" variant="ghost" icon="terminal" onClick={() => onOpenTerminal({ kind: "connection", id: connection.id, name })}>
      Show terminal
    </Button>
  );
  const cancelButton = <Button size="sm" onClick={end} disabled={cancel.isPending}>Cancel</Button>;
  const connect = (label: string, primary = true) => (
    <Button size="sm" variant={primary ? "primary" : "secondary"} onClick={begin} disabled={login.isPending}>{label}</Button>
  );

  let body: ReactNode;
  switch (view.kind) {
    case "checking":
      body = <span className={s.status}>Checking…</span>;
      break;
    case "connected":
      body = (
        <div className={s.buttons}>
          <span className={s.connected}><Icon name="check" />Connected</span>
          <Button size="sm" variant="ghost" onClick={begin} disabled={login.isPending}>Use another account</Button>
        </div>
      );
      break;
    case "signed-out":
      body = connect(`Connect ${name}`);
      break;
    case "unknown":
      body = (
        <div className={s.buttons}>
          {connection.check && <span className={s.status}>Couldn't check the sign-in</span>}
          {connect(`Connect ${name}`, false)}
        </div>
      );
      break;
    case "getting-link":
      body = (
        <>
          <span className={s.status}>Getting the sign-in link…</span>
          <div className={s.buttons}>{cancelButton}{showTerminal}</div>
        </>
      );
      break;
    case "waiting":
      body = (
        <>
          <div className={s.buttons}>
            <a className={s.signIn} href={view.link} target="_blank" rel="noopener noreferrer">
              Open {name} sign-in<Icon name="external" />
            </a>
            {view.code && <span className={s.code}>Code: <strong>{view.code}</strong></span>}
          </div>
          <span className={s.status}>Waiting for you to finish in the browser…</span>
          <div className={s.buttons}>{cancelButton}{showTerminal}</div>
        </>
      );
      break;
    case "asking":
      body = (
        <>
          <QuestionField
            key={`${view.asking.source}:${view.asking.id}`}
            asking={view.asking}
            onSend={send}
            sending={reply.isPending}
            error={replyProblem}
          />
          <div className={s.buttons}>{cancelButton}{showTerminal}</div>
        </>
      );
      break;
    case "failed":
      body = (
        <>
          <span className={s.problem}>Didn't connect</span>
          <div className={s.buttons}>{connect("Try again")}{showTerminal}</div>
        </>
      );
      break;
    case "timed-out":
      body = (
        <>
          <span className={s.problem}>Took too long</span>
          <div className={s.buttons}>{connect("Try again")}</div>
        </>
      );
      break;
  }

  return (
    <Row name={name}>
      {body}
      {problem && <p className={s.error} role="alert">{problem}</p>}
    </Row>
  );
}

export function ConnectionRow(props: Props) {
  if (props.connection.login) return <LoginRow {...props} />;
  return (
    <Row name={props.connection.name}>
      <span className={s.status}>asked during the build if {props.connection.name} needs it</span>
    </Row>
  );
}
