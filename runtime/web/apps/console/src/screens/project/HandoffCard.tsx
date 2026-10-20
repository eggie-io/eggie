import type { ReactNode } from "react";
import { Link } from "react-router";
import { Badge, Button, Card, COPY_REFUSED, cx, useCopy } from "@eggie/ui";
import type { CurrentAgent } from "../../agents/current";
import s from "./ProjectPage.module.css";

export interface HandoffSteps {
  // Copied as is; `extra`, when set, is its last paragraph, shown dimmed on its own line.
  prompt: string;
  extra?: string;
  copyHint: string;
  pasteHint: string;
  last: ReactNode;
}

function Step({ n, last = false, children }: { n: number; last?: boolean; children: ReactNode }) {
  return (
    <li className={cx(s.step, last && s.stepLast)}>
      <span className={s.stepNumber} aria-hidden="true">{n}</span>
      <div className={s.stepBody}>{children}</div>
    </li>
  );
}

export function StepText({ title, hint, action }: { title: ReactNode; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className={s.stepText}>
      <div className={s.stepWords}>
        <span className={s.stepTitle}>{title}</span>
        {hint && <span className={s.stepHint}>{hint}</span>}
      </div>
      {action}
    </div>
  );
}

export function HandoffCard({ title, agent, steps }: { title: string; agent: CurrentAgent; steps: HandoffSteps }) {
  const { prompt, extra } = steps;
  const { copied, refused, copy, target } = useCopy<HTMLDivElement>(prompt);
  const head = extra && prompt.endsWith(extra) ? prompt.slice(0, -extra.length).trimEnd() : prompt;

  return (
    <Card as="section" className={s.handoff}>
      <header className={s.handoffHead}>
        <h2 className={s.handoffTitle}>{title}</h2>
        <div className={s.handoffAside}>
          {agent.connected ? <Badge tone="success" icon="check-circle">Connected</Badge> : <Badge>Not connected</Badge>}
          <Link to="/agents" className={s.switchAgent}>Switch agent</Link>
        </div>
      </header>
      <ol className={s.steps}>
        <Step n={1}>
          <span className={s.stepTitle}>Copy this prompt</span>
          <div ref={target} className={s.promptBlock}>
            <span>{head}</span>
            {extra && <span className={s.promptExtra}>{extra}</span>}
          </div>
          <div className={s.row}>
            <Button variant="primary" icon={copied ? "check" : "copy"} onClick={copy}>
              {copied ? "Copied" : "Copy the prompt"}
            </Button>
            <span className={s.note} role={refused ? "alert" : undefined}>{refused ? COPY_REFUSED : steps.copyHint}</span>
          </div>
        </Step>
        <Step n={2}>
          <StepText title={`Paste it into ${agent.name} and press Enter`} hint={steps.pasteHint} />
        </Step>
        <Step n={3} last>{steps.last}</Step>
      </ol>
    </Card>
  );
}
