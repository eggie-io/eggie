import { useEffect, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { Button, Icon, IconButton, Notice, StateBadge } from "@eggie/ui";
import { Elapsed } from "../../components/Elapsed";
import { projectColor } from "../../projects/color";
import { CAUSE_COPY, actionError, primaryUrl } from "../../projects/copy";
import { hostOf } from "../../projects/format";
import { publicView } from "../../projects/public";
import { useLifecycle } from "../../projects/queries";
import type { Project } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import { projectView } from "../../projects/view";
import s from "./ProjectList.module.css";
import { openExternal } from "../../desktop/desktop";

export function ProjectRow({ project }: { project: Project }) {
  const view = projectView(project);
  const lifecycle = useLifecycle(project.id);
  const navigate = useNavigate();
  const now = useNow(30_000);
  const pub = publicView(project.public, now);
  const primary = primaryUrl(project);
  const page = `/p/${encodeURIComponent(project.id)}`;
  const look = () => navigate(page);
  const tile = projectColor(project.id);

  // Clear a stale error notice from a previous action once the row moves to
  // a different view (e.g. a failed Start shouldn't still show once it's running).
  useEffect(() => {
    lifecycle.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.kind]);

  let line: ReactNode = null;
  let actions: ReactNode = null;
  switch (view.kind) {
    case "running":
      line = primary && <a className={s.address} href={primary} target="_blank" rel="noopener noreferrer">{hostOf(primary)}</a>;
      actions = (
        <>
          {primary && (
            <Button aria-label={`Open ${project.id}`} size="sm" iconRight="external" onClick={() => openExternal(primary)}>
              Open
            </Button>
          )}
          <Button
            aria-label={`Stop ${project.id}`}
            variant="ghost"
            size="sm"
            icon="stop"
            disabled={lifecycle.isPending}
            onClick={() => lifecycle.mutate("down")}
          >
            Stop
          </Button>
        </>
      );
      break;
    case "stopped":
      line = primary && <span className={s.addressMuted}>{hostOf(primary)}</span>;
      actions = (
        <Button
          aria-label={`Start ${project.id}`}
          size="sm"
          icon="play"
          disabled={lifecycle.isPending}
          onClick={() => lifecycle.mutate("up")}
        >
          Start
        </Button>
      );
      break;
    case "starting":
      line = primary && <span className={s.addressMuted}>{hostOf(primary)}</span>;
      actions = <span className={s.quiet}><Elapsed startedAt={view.job.started_at} /> so far</span>;
      break;
    case "stopping":
      line = <span className={s.quiet}>Putting it away…</span>;
      break;
    case "setup":
      line = <span className={s.trouble}>{CAUSE_COPY.unreadable.short}</span>;
      actions = (
        <Button aria-label={`Take a look at ${project.id}`} size="sm" iconRight="chevron-right" onClick={look}>
          Take a look
        </Button>
      );
      break;
    case "wrong":
      line = <span className={s.trouble}>{CAUSE_COPY[view.cause].short}</span>;
      actions = (
        <Button aria-label={`Take a look at ${project.id}`} size="sm" iconRight="chevron-right" onClick={look}>
          Take a look
        </Button>
      );
      break;
    case "gone":
      line = <span className={s.trouble}>Its folder has gone missing</span>;
      actions = (
        <Button aria-label={`Take a look at ${project.id}`} size="sm" iconRight="chevron-right" onClick={look}>
          Take a look
        </Button>
      );
      break;
    case "waiting":
      line = <span className={s.quiet}>Waiting for your coding agent</span>;
      actions = (
        <Button aria-label={`Open page for ${project.id}`} size="sm" iconRight="chevron-right" onClick={look}>
          Open page
        </Button>
      );
      break;
  }

  // wrong/gone/waiting already lead to the page through their only action.
  const settings = view.kind !== "wrong" && view.kind !== "setup" && view.kind !== "gone" && view.kind !== "waiting" && (
    <IconButton icon="settings" label={`Settings for ${project.id}`} size="sm" onClick={look} />
  );

  return (
    <li className={s.row}>
      <span className={s.tile} style={{ background: tile.bg, color: tile.ink }} aria-hidden="true">
        {project.id.charAt(0)}
      </span>
      <div className={s.who}>
        <Link className={s.name} to={page}>{project.id}</Link>
        {line}
        {pub.kind === "on" && (
          <span className={s.public}>
            <Icon name="globe" />
            {pub.left ? `Public · ${pub.left}` : "Public"}
          </span>
        )}
      </div>
      <div className={s.state}><StateBadge state={view.badge} /></div>
      <div className={s.actions}>{actions}{settings}</div>
      {lifecycle.error && <Notice tone="danger" className={s.rowError}>{actionError(lifecycle.error)}</Notice>}
    </li>
  );
}
