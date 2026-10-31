import type { ReactNode } from "react";
import { Link } from "react-router";
import { Badge, Card, Icon } from "@eggie/ui";
import { useAgents, useAgentStatus } from "../../agents/queries";
import { SkipButton } from "../onboarding/Onboarding";
import { AgentMark } from "./AgentMark";
import s from "./Agents.module.css";

export type AgentBase = "/agents" | "/welcome";

export function AgentPicker({ base }: { base: AgentBase }) {
  const welcome = base === "/welcome";
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
            <Link className={s.cardLink} to={`${base}/${agent.id}`}>
              <Card className={s.card}>
                <span className={s.cardTop}>
                  <AgentMark agent={agent} />
                  <span className={s.cardEnd}>
                    {status.data?.agents[agent.id]?.connected && <Badge tone="success" icon="check-circle">Connected</Badge>}
                    <Icon name="chevron-right" className={s.cardChevron} />
                  </span>
                </span>
                <span className={s.cardText}>
                  <span className={s.cardName}>
                    {agent.name}
                    {agent.vendor && <span className={s.vendor}> by {agent.vendor}</span>}
                  </span>
                  <span className={s.cardTagline}>{agent.platforms[platform]!.tagline}</span>
                </span>
              </Card>
            </Link>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className={s.page}>
      <div className={s.heading}>
        <h1 className={s.title}>{welcome ? "Which coding agent do you use?" : "Which agent do you use?"}</h1>
        <p className={s.sub}>
          {welcome
            ? "Eggie works with the agent you already have. Pick it and we'll connect it to your kitchen. It takes about two minutes."
            : "Pick one and we'll walk you through it. Takes about two minutes."}
        </p>
      </div>
      {body}
      {welcome && (
        <div className={s.skipRow}>
          <p className={s.muted}>Don't see yours? You can connect one any time from Agents.</p>
          <SkipButton />
        </div>
      )}
    </div>
  );
}
