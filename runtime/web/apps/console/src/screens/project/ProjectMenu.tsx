import { useEffect, useRef, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";
import { Icon, StateBadge, cx, type IconName } from "@eggie/ui";
import { projectColor } from "../../projects/color";
import type { Project } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import { projectView } from "../../projects/view";
import { useSite } from "../../site/SiteContext";
import { filesRoute } from "../../uploads/paths";
import { menuNotes } from "./menu";
import s from "./ProjectLayout.module.css";

export type ProjectDialog = "public" | "push" | "delete";

function Label({ icon, children, note, noteClass }: { icon: IconName; children: ReactNode; note?: string | null; noteClass?: string }) {
  return (
    <>
      <Icon name={icon} />
      <span className={s.text}>
        <span className={s.label}>{children}</span>
        {note && <span className={cx(s.note, noteClass)}>{note}</span>}
      </span>
    </>
  );
}

// `project` is undefined while it loads or once it's gone: only the way back and the name stay.
export type PushItem = { note: string | null; disabled: boolean } | null;

export function ProjectMenu({ id, project, push, onOpen }: {
  id: string;
  project?: Project;
  push: PushItem;
  onOpen: (dialog: ProjectDialog) => void;
}) {
  const site = useSite();
  const now = useNow();
  const tile = projectColor(id);
  const page = `/p/${encodeURIComponent(id)}`;
  const notes = project ? menuNotes(project, site, now) : null;

  const menu = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  useEffect(() => {
    // Below 760px the menu is a scrolling row; bring the current item into it
    // unless half of it already shows, so "All projects" stays in view on the overview.
    const row = menu.current;
    const current = row?.querySelector<HTMLElement>("[aria-current='page']");
    if (!row || !current || row.scrollWidth <= row.clientWidth) return;
    const box = row.getBoundingClientRect();
    const item = current.getBoundingClientRect();
    const middle = (item.left + item.right) / 2;
    if (middle > box.right) row.scrollLeft += item.right - box.right + 16;
    else if (middle < box.left) row.scrollLeft -= box.left - item.left + 16;
  }, [pathname, project !== undefined]);

  return (
    <aside ref={menu} className={s.menu}>
      <div className={s.top}>
        <Link to="/" className={s.allProjects}>
          <Icon name="chevron-left" />
          All projects
        </Link>
        <div className={s.tile}>
          <span className={s.square} style={{ background: tile.bg, color: tile.ink }} aria-hidden="true">
            {id.charAt(0)}
          </span>
          <div className={s.who}>
            <span className={s.name} title={id}>{id}</span>
            {project && <span className={s.badge}><StateBadge state={projectView(project).badge} /></span>}
          </div>
        </div>
        {notes && (
          <nav className={s.items} aria-label={`${id} menu`}>
            <NavLink to={page} end className={s.item}>
              <Label icon="layers">Overview</Label>
            </NavLink>
            <NavLink to={`${page}/analyze`} className={s.item}>
              <Label icon="search">Analyze</Label>
            </NavLink>
            <NavLink to={filesRoute(id, "")} className={s.item}>
              <Label icon="folder">Files</Label>
            </NavLink>
            <NavLink to={`${page}/secrets`} className={s.item}>
              <Label icon="key" note={notes.secrets} noteClass={s.noteWarning}>Secrets</Label>
            </NavLink>
            {notes.public && (
              <button
                type="button"
                className={s.item}
                disabled={notes.public.disabled}
                title={notes.public.disabled ? notes.public.note : undefined}
                onClick={() => onOpen("public")}
              >
                <Label icon="globe" note={notes.public.note}>Public address</Label>
              </button>
            )}
            {site === null && push && (
              <button
                type="button"
                className={s.item}
                disabled={push.disabled}
                title={push.disabled ? (push.note ?? undefined) : undefined}
                onClick={() => onOpen("push")}
              >
                <Label icon="upload" note={push.note}>Push to Kitchen</Label>
              </button>
            )}
          </nav>
        )}
      </div>
      {notes && (
        <div className={s.bottom}>
          <button type="button" className={cx(s.item, s.danger)} onClick={() => onOpen("delete")}>
            <Label icon="trash">Delete project</Label>
          </button>
        </div>
      )}
    </aside>
  );
}
