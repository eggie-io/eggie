import { Link, useLocation } from "react-router";
import { cx } from "@eggie/ui";
import { PLUG, PROJECTS } from "../screens/icons";
import s from "./Shell.module.css";

export function Tabs() {
  const onAgents = /^\/agents(\/|$)/.test(useLocation().pathname);
  return (
    <>
      <Link className={cx(s.tab, !onAgents && s.active)} to="/" aria-current={onAgents ? undefined : "page"}>
        {PROJECTS}<span className={s.tabLabel}>Projects</span>
      </Link>
      <Link className={cx(s.tab, onAgents && s.active)} to="/agents" aria-current={onAgents ? "page" : undefined}>
        {PLUG}<span className={s.tabLabel}>Agents</span>
      </Link>
    </>
  );
}
