import type { ReactNode } from "react";
import { Link } from "react-router";
import { useAgents, useAgentStatus } from "../../agents/queries";
import { CHEVRON_RIGHT } from "../icons";
import s from "./Agents.module.css";

export function AgentPicker() {
  const { platform, agents, error } = useAgents();
  const status = useAgentStatus();

  let body: ReactNode;
  if (error) body = <p className={s.error}>Couldn't load the agent guides. Reload the page to try again.</p>;
  else if (agents === undefined) body = <p className={s.muted}>Getting the guides…</p>;
  else if (agents.length === 0) body = <p className={s.muted}>No guides for this computer yet.</p>;
  else {
    body = (
      <ul className={s.grid}>
        {agents.map((agent) => (
          <li key={agent.id}>
            <Link className={s.card} to={`/agents/${agent.id}`}>
              <span className={s.cardTop}>
                <img className={s.icon} src={agent.icon} alt="" width={40} height={40} />
                {status.data?.agents[agent.id]?.connected && <span className={s.badge}>Connected</span>}
                {CHEVRON_RIGHT}
              </span>
              <span className={s.cardText}>
                <span className={s.cardName}>{agent.name}</span>
                <span className={s.cardTagline}>{agent.platforms[platform]!.tagline}</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className={s.page}>
      <div>
        <h1 className={s.title}>Which agent do you use?</h1>
        <p className={s.sub}>Pick one and we'll walk you through it. Takes about two minutes.</p>
      </div>
      {body}
    </div>
  );
}
