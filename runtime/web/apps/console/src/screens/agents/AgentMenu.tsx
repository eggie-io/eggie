import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Button, Icon, cx } from "@eggie/ui";
import type { Agent } from "../../agents/catalog";
import { AgentMark } from "./AgentMark";
import type { AgentBase } from "./AgentPicker";
import s from "./Agents.module.css";

export function AgentMenu({ agents, current, base }: { agents: Agent[]; current: Agent; base: AgentBase }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className={s.menuRoot} ref={root}>
      <Button variant="secondary" iconRight="chevron-down" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(!open)}>
        {current.name}
      </Button>
      {open && (
        <div className={s.menu} role="menu">
          {agents.map((agent) => (
            <Link
              key={agent.id}
              role="menuitem"
              to={`${base}/${agent.id}`}
              className={cx(s.menuItem, agent.id === current.id && s.menuCurrent)}
              onClick={() => setOpen(false)}
            >
              <AgentMark agent={agent} small />
              <span className={s.menuName}>{agent.name}</span>
              {agent.id === current.id && <Icon name="check" />}
            </Link>
          ))}
          <span className={s.menuRule} aria-hidden="true" />
          <Link role="menuitem" to={base} className={s.menuItem} onClick={() => setOpen(false)}>
            See all agents
          </Link>
        </div>
      )}
    </div>
  );
}
