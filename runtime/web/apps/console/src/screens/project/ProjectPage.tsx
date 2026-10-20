import { useEffect, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router";
import { Button, Notice, PromptCard } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { Elapsed } from "../../components/Elapsed";
import { actionError, primaryUrl } from "../../projects/copy";
import { waitingPrompt } from "../../projects/prompts";
import { publicView } from "../../projects/public";
import { useDeleteProject, useLifecycle, useProject } from "../../projects/queries";
import { useNow } from "../../projects/useNow";
import { projectView } from "../../projects/view";
import { AddressRows } from "./AddressRows";
import { StartingBody } from "./StartingBody";
import { TitleRow } from "./TitleRow";
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

  // Checked before the data check: TanStack keeps stale data around on a
  // failed refetch, so a project deleted elsewhere would otherwise keep
  // showing its old body instead of this notice.
  if (query.error instanceof ApiError && query.error.code === "project_not_found") {
    return (
      <section className={s.page}>
        <TitleRow title={`No project called ${id}`} />
      </section>
    );
  }

  if (query.data === undefined || view === null) {
    return (
      <section className={s.page}>
        <TitleRow title={id} />
        {query.isError ? <Notice tone="danger">{query.error.message}</Notice> : <p className={s.muted}>Checking the kitchen…</p>}
      </section>
    );
  }

  const project = query.data;
  const primary = primaryUrl(project);
  const publicUrls = pub?.kind === "on" ? pub.urls : undefined;
  const failed = lifecycle.error && <Notice tone="danger">{actionError(lifecycle.error)}</Notice>;
  const restartNotice = project.restart_needed && (
    <Notice tone="warning">Secrets changed — restart the project to apply them.</Notice>
  );

  let body: ReactNode;
  switch (view.kind) {
    case "starting":
      body = <StartingBody project={project} job={view.job} />;
      break;
    case "stopping":
      body = (
        <TitleRow
          title={project.id}
          sub={<>Putting {project.id} away… <Elapsed startedAt={view.job.started_at} /></>}
        />
      );
      break;
    case "wrong":
      body = (
        <>
          <WrongBody project={project} cause={view.cause} detail={view.detail} />
          {restartNotice}
        </>
      );
      break;
    case "running": {
      const count = project.web.length;
      body = (
        <>
          <TitleRow
            title={project.id}
            sub={count > 0 ? `Running · ${count} ${count === 1 ? "address" : "addresses"} open` : "Running"}
            actions={
              <>
                <Button variant="ghost" icon="refresh" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart")}>
                  Restart
                </Button>
                <Button variant="secondary" icon="stop" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("down")}>
                  Stop
                </Button>
                {primary && (
                  <Button variant="primary" iconRight="external" onClick={() => openExternal(primary)}>
                    Open in browser
                  </Button>
                )}
              </>
            }
          />
          {failed}
          {restartNotice}
          <AddressRows web={project.web} live publicUrls={publicUrls} />
        </>
      );
      break;
    }
    case "stopped":
      body = (
        <>
          <TitleRow
            title={project.id}
            sub="Stopped"
            actions={
              <Button variant="primary" icon="play" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("up")}>
                Start
              </Button>
            }
          />
          {failed}
          <AddressRows web={project.web} live={false} publicUrls={publicUrls} />
        </>
      );
      break;
    case "waiting":
      body = (
        <>
          <TitleRow title={project.id} sub="Nothing to cook yet" />
          <p className={s.lead}>
            {project.id} is an empty folder at ~/projects/{project.id}. Your coding agent fills it in; once there's a
            docker-compose.yml, Start appears here.
          </p>
          <PromptCard prompt={waitingPrompt(project.id)} />
        </>
      );
      break;
    case "gone":
      body = (
        <>
          <TitleRow title={`The folder for ${project.id} has gone missing`} />
          <p className={s.lead}>Eggie still remembers it, but ~/projects/{project.id} isn't there any more.</p>
          <div className={s.row}>
            <Button
              variant="danger"
              disabled={forget.isPending}
              onClick={() => forget.mutate(false, { onSuccess: (result) => navigate("/", { state: { deleted: result } }) })}
            >
              Forget it
            </Button>
          </div>
          {forget.error && <Notice tone="danger">{actionError(forget.error)}</Notice>}
        </>
      );
      break;
  }

  return (
    <section className={s.page}>
      {query.isError && <Notice tone="danger">{query.error.message}</Notice>}
      {body}
    </section>
  );
}
