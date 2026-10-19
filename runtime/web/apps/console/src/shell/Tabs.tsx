import { Link, useLocation } from "react-router";
import { Icon } from "@eggie/ui";
import { useProjects } from "../projects/queries";
import s from "./Shell.module.css";

export function Tabs() {
  const onAgents = /^\/agents(\/|$)/.test(useLocation().pathname);
  const count = useProjects().data?.projects.length;
  return (
    <>
      <Link className={s.tab} to="/"
        aria-current={onAgents ? undefined : "page"}
        aria-label={count === undefined ? undefined : `Projects, ${count} ${count === 1 ? "project" : "projects"}`}
      >
        <Icon name="layers" />Projects
        {count !== undefined && <span className={s.count}>{count}</span>}
      </Link>
      <Link className={s.tab} to="/agents" aria-current={onAgents ? "page" : undefined}>
        <Icon name="terminal" />Agents
      </Link>
    </>
  );
}
