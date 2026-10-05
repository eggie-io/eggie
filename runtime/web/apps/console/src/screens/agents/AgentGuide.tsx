import { useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router";
import { Button, cx } from "@omelet/ui";
import type { Agent, Connect, Guide } from "../../agents/catalog";
import { cardRows } from "../../agents/card";
import { useAgents } from "../../agents/queries";
import { CopyButton } from "../../components/CopyButton";
import { AgentMenu } from "./AgentMenu";
import { AgentStatusLine } from "./AgentStatusLine";
import s from "./Agents.module.css";

export function AgentGuide() {
  const { id } = useParams();
  const { platform, agents, connect, error } = useAgents();

  if (error) return <p className={s.error}>Couldn't load the agent guides. Reload the page to try again.</p>;
  if (agents === undefined) return <p className={s.muted}>Getting the guide…</p>;
  const agent = agents.find((candidate) => candidate.id === id);
  if (!agent) return <Navigate to="/agents" replace />;

  return (
    <div className={s.page}>
      <div className={s.guideHead}>
        <h1 className={s.guideTitle}>Connect a coding agent</h1>
        <AgentMenu agents={agents} current={agent} />
      </div>
      {/* Keyed so switching agent starts again at step 1. */}
      <Steps key={agent.id} agent={agent} guide={agent.platforms[platform]!} connect={connect} />
    </div>
  );
}

function Steps({ agent, guide, connect }: { agent: Agent; guide: Guide; connect: Connect | undefined }) {
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const rows = cardRows(guide, connect);
  const step = guide.steps[active];
  const last = active === guide.steps.length - 1;

  return (
    <div className={s.guide}>
      <div className={s.side}>
        <ol className={s.steps}>
          {guide.steps.map((item, index) => (
            <li key={index}>
              <button
                type="button"
                className={cx(s.step, index === active && s.stepActive)}
                aria-current={index === active ? "step" : undefined}
                onClick={() => setActive(index)}
              >
                <span className={cx(s.stepDot, index <= active && s.stepDotOn)}>{index + 1}</span>
                <span className={s.stepText}>
                  <span className={s.stepTitle}>{item.title}</span>
                  {index === active && <span className={s.stepBody}>{item.body}</span>}
                </span>
              </button>
            </li>
          ))}
        </ol>
        <AgentStatusLine agent={agent} />
        {rows && (
          <section className={s.key} aria-label="Connection details">
            <h2 className={s.keyLabel}>Your kitchen's key</h2>
            {rows.map((row) => (
              <div key={row.label} className={s.keyRow}>
                <span className={s.keyName}>{row.label}</span>
                <span className={cx(s.keyValue, !row.copy && s.keyUnknown)} title={row.value}>{row.value}</span>
                {row.copy && <CopyButton className={s.copy} text={row.value} />}
              </div>
            ))}
            <p className={s.keyNote}>{agent.name} asks for these once. If it can't connect on this port, ask us for help.</p>
          </section>
        )}
      </div>
      <div className={s.main}>
        <div className={s.frame}>
          {step.screenshot ? (
            <img className={s.shot} src={step.screenshot} alt={step.alt} />
          ) : (
            <div className={s.placeholder}>{step.alt}</div>
          )}
        </div>
        <div className={s.nav}>
          <span className={s.count}>Step {active + 1} of {guide.steps.length}</span>
          <Button variant="secondary" disabled={active === 0} onClick={() => setActive(active - 1)}>Back</Button>
          <Button variant="primary" onClick={() => (last ? navigate("/") : setActive(active + 1))}>
            {last ? "Done" : "Next"}
          </Button>
        </div>
      </div>
    </div>
  );
}
