import { useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router";
import { Button, cx } from "@omelet/ui";
import type { Agent, Connect, Guide } from "../../agents/catalog";
import { cardRows } from "../../agents/card";
import { useAgents, useAgentStatus } from "../../agents/queries";
import { CopyButton } from "../../components/CopyButton";
import { useOnboarding } from "../onboarding/Onboarding";
import { AgentMenu } from "./AgentMenu";
import type { AgentBase } from "./AgentPicker";
import { AgentStatusLine } from "./AgentStatusLine";
import s from "./Agents.module.css";

export function AgentGuide({ base }: { base: AgentBase }) {
  const { id } = useParams();
  const { platform, agents, connect, error } = useAgents();

  if (error) return <p className={s.error}>Couldn't load the agent guides. Reload the page to try again.</p>;
  if (agents === undefined) return <p className={s.muted}>Getting the guide…</p>;
  const agent = agents.find((candidate) => candidate.id === id);
  if (!agent) return <Navigate to={base} replace />;

  return (
    <div className={s.page}>
      <div className={s.guideHead}>
        <h1 className={s.guideTitle}>Connect a coding agent</h1>
        <AgentMenu agents={agents} current={agent} base={base} />
      </div>
      {/* Keyed so switching agent starts again at step 1. */}
      <Steps key={agent.id} agent={agent} guide={agent.platforms[platform]!} connect={connect} welcome={base === "/welcome"} />
    </div>
  );
}

type Check = "idle" | "checking" | "notYet" | "unreachable";

const CHECK_TEXT: Record<"notYet" | "unreachable", (name: string) => string> = {
  notYet: (name) => `Not connected yet. Finish the last step in ${name}, then check again.`,
  unreachable: () => "Couldn't reach your kitchen just now. Check again in a moment.",
};

function Steps({ agent, guide, connect, welcome }: { agent: Agent; guide: Guide; connect: Connect | undefined; welcome: boolean }) {
  const [active, setActiveStep] = useState(0);
  const [check, setCheck] = useState<Check>("idle");
  const navigate = useNavigate();
  const { finish } = useOnboarding();
  const status = useAgentStatus(agent.id);
  const connected = status.data?.agents[agent.id]?.connected === true;
  const rows = cardRows(guide, connect);
  const step = guide.steps[active];
  const last = active === guide.steps.length - 1;
  const setActive = (index: number) => {
    setActiveStep(index);
    setCheck("idle");
  };

  const checkConnection = async () => {
    setCheck("checking");
    const result = await status.refetch();
    if (result.data?.agents[agent.id]?.connected) finish(agent.name);
    else setCheck(result.isError ? "unreachable" : "notYet");
  };

  let primary;
  if (welcome && connected) primary = <Button variant="primary" onClick={() => finish(agent.name)}>Continue</Button>;
  else if (welcome && last)
    primary = (
      <Button variant="primary" disabled={check === "checking"} onClick={checkConnection}>
        {check === "checking" ? "Checking…" : "Check the connection"}
      </Button>
    );
  else
    primary = (
      <Button variant="primary" onClick={() => (last ? navigate("/") : setActive(active + 1))}>
        {last ? "Done" : "Next"}
      </Button>
    );
  const problem = welcome && !connected && (check === "notYet" || check === "unreachable") ? CHECK_TEXT[check](agent.name) : null;

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
          {problem ? (
            <span className={s.checkProblem} role="status">{problem}</span>
          ) : (
            <span className={s.count}>Step {active + 1} of {guide.steps.length}</span>
          )}
          <Button variant="secondary" disabled={active === 0} onClick={() => setActive(active - 1)}>Back</Button>
          {primary}
        </div>
      </div>
    </div>
  );
}
