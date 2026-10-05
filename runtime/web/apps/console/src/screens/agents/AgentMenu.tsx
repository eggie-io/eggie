import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { cx } from "@omelet/ui";
import type { Agent } from "../../agents/catalog";
import type { AgentBase } from "./AgentPicker";
import { CHECK, CHEVRON_DOWN } from "../icons";
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
      <button type="button" className={s.menuButton} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(!open)}>
        <img className={s.iconSmall} src={current.icon} alt="" width={26} height={26} />
        {current.name}
        {CHEVRON_DOWN}
      </button>
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
              <img className={s.iconSmall} src={agent.icon} alt="" width={24} height={24} />
              <span className={s.menuName}>{agent.name}</span>
              {agent.id === current.id && CHECK}
            </Link>
          ))}
          <span className={s.menuRule} aria-hidden="true" />
          <Link role="menuitem" to={base} className={s.menuAll} onClick={() => setOpen(false)}>
            See all agents
          </Link>
        </div>
      )}
    </div>
  );
}
