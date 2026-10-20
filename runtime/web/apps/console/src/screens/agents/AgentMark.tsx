import { useState } from "react";
import { cx } from "@eggie/ui";
import type { Agent } from "../../agents/catalog";
import s from "./Agents.module.css";

const initials = (name: string) =>
  name.split(/\s+/).map((word) => word.charAt(0)).join("").slice(0, 2).toUpperCase();

// The agent's icon, or its initials when the icon file doesn't load.
export function AgentMark({ agent, small = false }: { agent: Pick<Agent, "name" | "icon">; small?: boolean }) {
  const [broken, setBroken] = useState(false);
  return (
    <span className={cx(s.mark, small && s.markSmall)} aria-hidden="true">
      {broken ? initials(agent.name) : <img src={agent.icon} alt="" onError={() => setBroken(true)} />}
    </span>
  );
}
