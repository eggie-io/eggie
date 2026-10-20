import { useEffect, useState } from "react";
import { Button, Icon, cx, type IconName } from "@eggie/ui";
import type { Agent } from "../../agents/catalog";
import { POLL_MS, useAgentStatus, useEnsureSetup } from "../../agents/queries";
import { statusLine, type LineKind } from "../../agents/status";
import s from "./Agents.module.css";

const KIND: Record<LineKind, { className: string | undefined; icon: IconName }> = {
  connected: { className: s.statusConnected, icon: "check-circle" },
  installing: { className: s.statusInstalling, icon: "refresh" },
  failed: { className: s.statusFailed, icon: "x-circle" },
  waiting: { className: undefined, icon: "clock" },
};

export function AgentStatusLine({ agent }: { agent: Agent }) {
  const status = useAgentStatus(agent.id);
  const setup = useEnsureSetup();
  const { mutate } = setup;
  const [retriedAt, setRetriedAt] = useState(0);

  useEffect(() => {
    if (agent.hasSetup) mutate(agent.id);
  }, [agent.id, agent.hasSetup, mutate]);

  if (status.error || status.data === undefined) return null;
  const line = statusLine(agent.name, status.data.agents[agent.id]);
  const kind = KIND[line.kind];
  // The status is a poll behind the runner, so "failed" can still show after a
  // retry was taken; a second click then would queue a whole second setup.
  const settling = status.dataUpdatedAt < retriedAt + 2 * POLL_MS;
  return (
    <div className={cx(s.status, kind.className)} role="status">
      <Icon name={kind.icon} size="md" className={s.statusIcon} />
      <span className={s.statusText}>{line.text}</span>
      {line.kind === "failed" && (
        <Button
          variant="secondary"
          size="sm"
          disabled={setup.isPending || settling}
          onClick={() => {
            setRetriedAt(Date.now());
            mutate(agent.id);
          }}
        >
          Retry
        </Button>
      )}
    </div>
  );
}
