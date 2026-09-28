import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Button, Egg, Notice, PromptCard, StateBadge } from "@omelet/ui";
import { ApiError } from "../../api/client";
import { Elapsed } from "../../components/Elapsed";
import { actionError, primaryUrl } from "../../projects/copy";
import { waitingPrompt } from "../../projects/prompts";
import { publicView, tileLine } from "../../projects/public";
import { useDeleteProject, useLifecycle, useProject } from "../../projects/queries";
import { useNow } from "../../projects/useNow";
import { projectView } from "../../projects/view";
import { ARROW } from "../icons";
import { AddressRows } from "./AddressRows";
import { AnalyzeModal } from "./AnalyzeModal";
import { DeleteModal } from "./DeleteModal";
import { PublicModal } from "./PublicModal";
import { StartingBody } from "./StartingBody";
import { Tiles } from "./Tiles";
import { WrongBody } from "./WrongBody";
import s from "./ProjectPage.module.css";
import { openExternal } from "../../desktop/desktop";

export function ProjectPage() {
  const { id = "" } = useParams();
  const query = useProject(id);
  const lifecycle = useLifecycle(id);
  const forget = useDeleteProject(id);
  const navigate = useNavigate();
  const now = useNow();
  const [modal, setModal] = useState<"analyze" | "delete" | "public" | null>(null);
  const view = query.data ? projectView(query.data) : null;
  const pub = query.data ? publicView(query.data.public, now) : null;

  // Clear a stale error notice from a previous action once the project has
  // moved to a different view (e.g. a failed Stop shouldn't still show once
  // a Start has begun). Kept above the early returns below so this hook
  // always runs, whatever query.data/query.error currently hold.
  useEffect(() => {
    lifecycle.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.kind]);

  const back = <Link to="/" className={s.back}>‹ All projects</Link>;

  // Checked before the data check: TanStack keeps stale data around on a
  // failed refetch, so a project deleted elsewhere would otherwise keep
  // showing its old body instead of this notice.
  if (query.error instanceof ApiError && query.error.code === "project_not_found") {
    return (
      <section className={s.page}>
        {back}
        <h1 className={s.name}>No project called {id}</h1>
      </section>
    );
  }

  if (query.data === undefined || view === null) {
    return (
      <section className={s.page}>
        {back}
        {query.isError ? <p className={s.error}>{query.error.message}</p> : <p className={s.muted}>Checking the kitchen…</p>}
      </section>
    );
  }

  const project = query.data;
  const primary = primaryUrl(project);
  const tiles = (
    <Tiles
      id={project.id}
      publicLine={tileLine(pub!)}
      publicDisabled={pub!.kind === "unavailable"}
      onPublic={() => setModal("public")}
      onAnalyze={() => setModal("analyze")}
      onDelete={() => setModal("delete")}
    />
  );
  const failed = lifecycle.error && <Notice>{actionError(lifecycle.error)}</Notice>;
  const head = (sub?: string) => (
    <header className={s.head}>
      <Egg size={44} tone={view.badge === "running" || view.badge === "starting" ? "yolk" : "cold"} />
      <div className={s.grow}>
        <h1 className={s.name}>{project.id}</h1>
        {sub && <p className={s.sub}>{sub}</p>}
      </div>
      <StateBadge state={view.badge} />
    </header>
  );

  let body: ReactNode;
  switch (view.kind) {
    case "starting":
      body = <StartingBody project={project} job={view.job} />;
      break;
    case "stopping":
      body = (
        <>
          {head()}
          <p className={s.lead}>Putting {project.id} away… <Elapsed startedAt={view.job.started_at} /></p>
        </>
      );
      break;
    case "wrong":
      body = (
        <>
          <WrongBody project={project} cause={view.cause} detail={view.detail} />
          {tiles}
        </>
      );
      break;
    case "running": {
      const count = project.web.length;
      body = (
        <>
          {head(count > 0 ? `${count} ${count === 1 ? "address" : "addresses"} open` : undefined)}
          <div className={s.actions}>
            {primary && (
              <Button variant="primary" onClick={() => openExternal(primary)}>
                Open in browser{ARROW}
              </Button>
            )}
            <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("down")}>Stop</Button>
            <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart")}>Restart</Button>
          </div>
          {failed}
          <AddressRows web={project.web} live publicUrls={pub?.kind === "on" ? pub.urls : undefined} />
          {tiles}
        </>
      );
      break;
    }
    case "stopped":
      body = (
        <>
          {head()}
          <div className={s.actions}>
            <Button variant="primary" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("up")}>Start</Button>
          </div>
          {failed}
          <AddressRows web={project.web} live={false} publicUrls={pub?.kind === "on" ? pub.urls : undefined} />
          {tiles}
        </>
      );
      break;
    case "waiting":
      body = (
        <>
          {head("Nothing to cook yet")}
          <p className={s.lead}>
            {project.id} is an empty folder at ~/projects/{project.id}. Your coding agent fills it in; once there's a
            docker-compose.yml, Start appears here.
          </p>
          <PromptCard prompt={waitingPrompt(project.id)} />
          {tiles}
        </>
      );
      break;
    case "gone":
      body = (
        <>
          {head()}
          <h2 className={s.big}>The folder for {project.id} has gone missing</h2>
          <p className={s.lead}>Omelet still remembers it, but ~/projects/{project.id} isn't there any more.</p>
          <div className={s.actions}>
            <Button
              variant="danger"
              disabled={forget.isPending}
              onClick={() => forget.mutate(false, { onSuccess: (result) => navigate("/", { state: { deleted: result } }) })}
            >
              Forget it
            </Button>
          </div>
          {forget.error && <Notice>{actionError(forget.error)}</Notice>}
        </>
      );
      break;
  }

  return (
    <section className={s.page}>
      {back}
      {query.isError && <Notice>{query.error.message}</Notice>}
      {body}
      <AnalyzeModal open={modal === "analyze"} onClose={() => setModal(null)} />
      <DeleteModal id={project.id} open={modal === "delete"} onClose={() => setModal(null)} />
      <PublicModal project={project} open={modal === "public"} onClose={() => setModal(null)} />
    </section>
  );
}
