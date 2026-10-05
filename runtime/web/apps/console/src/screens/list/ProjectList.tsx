import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Notice } from "@omelet/ui";
import { subtitle } from "../../projects/format";
import { ONE, useProjects } from "../../projects/queries";
import type { DeleteResult } from "../../projects/types";
import { projectView } from "../../projects/view";
import { GitHubModal } from "../github/GitHubModal";
import { useOnboarding } from "../onboarding/Onboarding";
import { PLUS } from "../icons";
import { DiscoveredBand } from "./DiscoveredBand";
import { EmptyCounter } from "./EmptyCounter";
import { NewProjectModal } from "./NewProjectModal";
import { ProjectRow } from "./ProjectRow";
import s from "./ProjectList.module.css";

function DeletedNotice({ result }: { result: DeleteResult }) {
  return result.stopped ? (
    <Notice>Threw out {result.id}.</Notice>
  ) : (
    <Notice>
      {result.id} is deleted. Some of its little machines may still be running — restarting Omelet from the desktop
      app clears them.
    </Notice>
  );
}

export function ProjectList() {
  const query = useProjects();
  const client = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [github, setGitHub] = useState(false);
  const deleted = (useLocation().state as { deleted?: DeleteResult } | null)?.deleted;
  const { connectedAgent } = useOnboarding();
  const modal = <NewProjectModal open={creating} onClose={() => setCreating(false)} />;

  // The single-project cache for a deleted project is dropped only once the
  // list has taken over navigation, so a project page still mid-unmount never
  // re-fetches into a 404 before it's replaced by this screen.
  useEffect(() => {
    if (deleted) client.removeQueries({ queryKey: ONE(deleted.id) });
  }, [deleted, client]);

  let body: ReactNode;
  if (query.data === undefined) {
    body = query.isError ? <p className={s.error}>{query.error.message}</p> : <p className={s.muted}>Checking the kitchen…</p>;
  } else {
    const { projects, discovered } = query.data;
    if (projects.length === 0 && discovered.length === 0) {
      body = (
        <>
          {deleted && <DeletedNotice result={deleted} />}
          <EmptyCounter onNew={() => setCreating(true)} onGitHub={() => setGitHub(true)} connectedAgent={connectedAgent} />
        </>
      );
    } else {
      const cooking = projects.filter((project) => projectView(project).kind === "running").length;
      body = (
        <section className={s.page}>
          {deleted && <DeletedNotice result={deleted} />}
          {query.isError && <Notice>{query.error.message}</Notice>}
          <header className={s.head}>
            <div>
              <h1 className={s.title}>Your projects</h1>
              <p className={s.sub}>{subtitle(projects.length, cooking)}</p>
            </div>
            <div className={s.actions}>
              <Button onClick={() => setGitHub(true)}>From GitHub</Button>
              <Button variant="primary" onClick={() => setCreating(true)}>{PLUS}New project</Button>
            </div>
          </header>
          {discovered.length > 0 && <DiscoveredBand folders={discovered} />}
          {projects.length > 0 && (
            <ul className={s.list}>
              {projects.map((project) => (
                <li key={project.id}><ProjectRow project={project} /></li>
              ))}
            </ul>
          )}
          <Notice>These addresses only work on this computer. Nothing is out on the internet.</Notice>
        </section>
      );
    }
  }

  return (
    <>
      {body}
      {modal}
      <GitHubModal open={github} onClose={() => setGitHub(false)} />
    </>
  );
}
