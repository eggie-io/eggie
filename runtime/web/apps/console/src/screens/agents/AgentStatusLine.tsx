import { useEffect, useState } from "react";
import { Button, cx } from "@omelet/ui";
import type { Agent } from "../../agents/catalog";
import { POLL_MS, useAgentStatus, useEnsureSetup } from "../../agents/queries";
import { statusLine, type LineKind } from "../../agents/status";
import s from "./Agents.module.css";

const KIND: Record<LineKind, string | undefined> = {
  connected: s.statusConnected, installing: undefined, failed: s.statusFailed, waiting: undefined,
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
  // The status is a poll behind the runner, so "failed" can still show after a
  // retry was taken; a second click then would queue a whole second setup.
  const settling = status.dataUpdatedAt < retriedAt + 2 * POLL_MS;
  return (
    <div className={cx(s.status, KIND[line.kind])} role="status">
      <span className={s.statusText}>{line.kind === "connected" ? "✓ " : ""}{line.text}</span>
      {line.kind === "failed" && (
        <Button
          variant="secondary"
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
