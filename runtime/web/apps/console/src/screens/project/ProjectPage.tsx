import { useEffect, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router";
import { Button, Notice, StateBadge } from "@eggie/ui";
import { atSentenceStart } from "../../agents/current";
import { useCurrentAgent } from "../../agents/queries";
import { ApiError } from "../../api/client";
import { Elapsed } from "../../components/Elapsed";
import { actionError, primaryUrl } from "../../projects/copy";
import { newPrompt, said, setupPrompt } from "../../projects/prompts";
import { publicView } from "../../projects/public";
import { useDeleteProject, useLifecycle, useProject, useStartWhenReady } from "../../projects/queries";
import { useNow } from "../../projects/useNow";
import type { Project } from "../../projects/types";
import { projectView, type ProjectView } from "../../projects/view";
import { AddressRows } from "./AddressRows";
import { HandoffCard, StepText } from "./HandoffCard";
import { StartingBody } from "./StartingBody";
import { TitleRow } from "./TitleRow";
import { WrongBody } from "./WrongBody";
import s from "./ProjectPage.module.css";
import { openExternal } from "../../desktop/desktop";

type HandoffView = Extract<ProjectView, { kind: "waiting" | "setup" }>;

function SetupLead({ project, agent }: { project: Project; agent: string }) {
  const fit = `and ${agent} can make it fit Eggie in one go.`;
  if (project.origin === "github") {
    const url = `https://github.com/${project.repo}`;
    const from = project.repo ? (
      <a
        className={s.leadLink}
        href={url}
        onClick={(event) => {
          event.preventDefault();
          openExternal(url);
        }}
      >
        {project.repo}
      </a>
    ) : "GitHub";
    return <>Copied from {from}. It doesn't start yet. That's normal: code from GitHub is set up for someone else's computer, {fit}</>;
  }
  if (project.origin === "folder") {
    return <>Copied from a folder on your computer. It doesn't start yet. That's normal: a project made somewhere else is set up for that place, {fit}</>;
  }
  return <>The code in <code className={s.leadCode}>~/projects/{project.id}</code> doesn't start yet. That's normal before its first run here, {fit}</>;
}

// Its own component so the agent status is only polled while a hand-off is on screen.
function HandoffBody({ project, view, onTryNow, pending }: { project: Project; view: HandoffView; onTryNow: () => void; pending: boolean }) {
  const agent = useCurrentAgent();
  const starts = (action?: ReactNode) => (
    <StepText
      title={`${atSentenceStart(agent.name)} starts it when it's done`}
      hint={<>It runs <code>eggie up</code>; this page updates by itself.</>}
      action={action}
    />
  );
  const title = `The next step happens in ${agent.name}`;

  if (view.kind === "waiting") {
    return (
      <>
        <p className={s.lead}>
          Eggie made an empty folder at <code className={s.leadCode}>~/projects/{project.id}</code>. Building what goes in
          it is {agent.name}'s job.
        </p>
        <HandoffCard
          title={title}
          agent={agent}
          steps={{
            prompt: newPrompt(project.id),
            copyHint: "It already says where the folder is.",
            pasteHint: "It asks what you want to build, then gets to work. Usually a few minutes.",
            last: starts(),
          }}
        />
      </>
    );
  }
  return (
    <>
      <p className={s.lead}><SetupLead project={project} agent={agent.name} /></p>
      <HandoffCard
        title={title}
        agent={agent}
        steps={{
          prompt: setupPrompt(project.id, project.origin, view.detail, view.source),
          extra: view.detail ? said(view.source, view.detail) : undefined,
          copyHint: view.detail ? `${view.source === "docker" ? "Docker's" : "Eggie's"} message is included, so you don't have to dig for it.` : "It already says where the folder is.",
          pasteHint: "It reads the project, makes it run here and tells you which keys to add in Secrets.",
          last: starts(
            <Button variant="ghost" size="sm" icon="refresh" disabled={pending} onClick={onTryNow}>
              Try now
            </Button>,
          ),
        }}
      />
    </>
  );
}

export function ProjectPage() {
  const { id = "" } = useParams();
  const query = useProject(id);
  const lifecycle = useLifecycle(id);
  const startWhenReady = useStartWhenReady(id);
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
  const failed = (lifecycle.error ?? startWhenReady.error) && (
    <Notice tone="danger">{actionError(lifecycle.error ?? startWhenReady.error)}</Notice>
  );
  const start = () =>
    project.secrets_requested > 0
      ? startWhenReady.mutate(undefined, { onSuccess: () => navigate(`/p/${encodeURIComponent(id)}/secrets`) })
      : lifecycle.mutate("up");
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
    case "waiting":
    case "setup":
      body = (
        <>
          <TitleRow title={project.id} sub={<StateBadge state={view.badge} />} />
          <HandoffBody project={project} view={view} pending={lifecycle.isPending} onTryNow={() => lifecycle.mutate("up")} />
          {failed}
          {restartNotice}
        </>
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
              <Button variant="primary" icon="play" disabled={lifecycle.isPending || startWhenReady.isPending} onClick={start}>
                Start
              </Button>
            }
          />
          {failed}
          <AddressRows web={project.web} live={false} publicUrls={publicUrls} />
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
