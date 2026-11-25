import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Badge, BrandMark, Button, Card, Icon } from "@eggie/ui";
import type { Agent } from "../../agents/catalog";
import { useAgents, useAgentStatus } from "../../agents/queries";
import { useDesktopHome } from "../../desktop/DesktopContext";
import { openExternal } from "../../desktop/desktop";
import { instancesQuery } from "../../instances/query";
import { useSite, useWebApp } from "../../site/SiteContext";
import { SkipButton } from "../onboarding/Onboarding";
import { AgentMark } from "./AgentMark";
import s from "./Agents.module.css";

export type AgentBase = "/agents" | "/welcome";

export function AgentPicker({ base }: { base: AgentBase }) {
  const welcome = base === "/welcome";
  const { agents, error } = useAgents();
  const status = useAgentStatus();
  // Inside the Eggie app's own window there is no app to send people to.
  const inApp = useDesktopHome() !== null;
  const connected = (agent: Agent) =>
    status.data?.agents[agent.id]?.connected && <Badge tone="success" icon="check-circle">Connected</Badge>;

  let body: ReactNode;
  if (error) body = <p className={s.error}>Couldn't load the agent guides. Reload the page to try again.</p>;
  else if (agents === undefined) body = <p className={s.muted}>Getting the guides…</p>;
  else if (agents.length === 0) body = <p className={s.muted}>No guides for this computer yet.</p>;
  else if (inApp) {
    body = (
      <ul className={s.grid}>
        {agents.map((agent) => (
          <li key={agent.id}>
            <Link className={s.cardLink} to={`${base}/${agent.id}`}>
              <Card className={s.card}>
                <span className={s.cardTop}>
                  <AgentMark agent={agent} />
                  <span className={s.cardEnd}>
                    {connected(agent)}
                    <Icon name="chevron-right" className={s.cardChevron} />
                  </span>
                </span>
                <AgentName agent={agent} />
              </Card>
            </Link>
          </li>
        ))}
      </ul>
    );
  } else {
    body = (
      <ul className={s.manualList}>
        {agents.map((agent) => (
          <li key={agent.id}>
            <Link className={s.manualRow} to={`${base}/${agent.id}`}>
              <AgentMark agent={agent} />
              <AgentName agent={agent} />
              <span className={s.cardEnd}>
                {connected(agent)}
                <Icon name="chevron-right" className={s.cardChevron} />
              </span>
            </Link>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className={s.page}>
      <div className={s.heading}>
        <h1 className={s.title}>{welcome ? "Connect your coding agent" : "Connect an agent"}</h1>
        <p className={s.sub}>
          {welcome
            ? "Eggie works with the agent you already have. It takes about two minutes."
            : "Eggie works with the agent you already have."}
        </p>
      </div>
      {inApp ? body : (
        <>
          <AppPanel />
          <section className={s.manual}>
            <div className={s.manualHead}>
              <h2 className={s.manualTitle}>Connect by hand</h2>
              <Badge tone="outline">Advanced</Badge>
            </div>
            <p className={s.manualSub}>Set up the SSH connection yourself, step by step.</p>
            {body}
          </section>
        </>
      )}
      {welcome && (
        <div className={s.skipRow}>
          <p className={s.muted}>Don't see yours? You can connect one any time from Agents.</p>
          <SkipButton />
        </div>
      )}
    </div>
  );
}

function AgentName({ agent }: { agent: Agent }) {
  return (
    <span className={s.cardText}>
      <span className={s.cardName}>
        {agent.name}
        {agent.note && <span className={s.nameNote}> {agent.note}</span>}
      </span>
      <span className={s.cardTagline}>{agent.guide!.tagline}</span>
    </span>
  );
}

// The Eggie app handles eggie:// links; this one names the kitchen whose agent picker it opens.
function AppPanel() {
  const site = useSite();
  const webApp = useWebApp();
  const onSite = typeof site === "string";
  const { data } = useQuery({ ...instancesQuery, staleTime: Infinity, enabled: onSite });
  const kitchen = onSite ? data?.instances.find((instance) => instance.current)?.id : "local";
  const link = `eggie://connect/${kitchen ? encodeURIComponent(kitchen) : ""}`;
  return (
    <section className={s.appPanel}>
      <span className={s.appMark}><BrandMark size={30} /></span>
      <div className={s.appText}>
        <div className={s.appTitleRow}>
          <h2 className={s.appTitle}>Connect with the Eggie app</h2>
          <Badge tone="gold">Recommended</Badge>
        </div>
        <p className={s.appSub}>
          The Eggie app on your computer connects your agent to this kitchen in one click. Nothing to copy or paste.
        </p>
        <div className={s.appActions}>
          <Button variant="primary" size="lg" iconRight="external" onClick={() => { window.location.href = link; }}>
            Open Eggie
          </Button>
          {webApp && (
            <a className={s.appGet} href={webApp} onClick={(event) => { event.preventDefault(); openExternal(webApp); }}>
              Don't have it? Get the Eggie app
            </a>
          )}
        </div>
      </div>
    </section>
  );
}
