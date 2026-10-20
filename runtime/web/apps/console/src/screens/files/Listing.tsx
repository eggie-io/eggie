import { Link } from "react-router";
import { Icon, cx } from "@eggie/ui";
import { relativeTime, size } from "../../projects/format";
import type { DirEntry } from "../../uploads/queries";
import { fileUrl, filesRoute, joinPath } from "../../uploads/paths";
import s from "./FilesPage.module.css";

export function Columns() {
  return (
    <div className={s.columns} aria-hidden="true">
      <span>Name</span>
      <span>Size</span>
      <span>Changed</span>
      <span />
    </div>
  );
}

export function Listing({ projectId, dir, entries, now }: { projectId: string; dir: string; entries: DirEntry[]; now: number }) {
  return (
    <ul className={s.rows}>
      {entries.map((entry) => {
        const path = joinPath(dir, entry.name);
        const folder = entry.kind === "folder";
        const detail = folder ? (entry.items === null ? "—" : `${entry.items} ${entry.items === 1 ? "item" : "items"}`) : size(entry.size ?? 0);
        const cells = (
          <>
            <span className={s.name}>
              <span className={s.nameIcon}>{folder && <Icon name="folder" />}</span>
              <span className={cx(s.nameText, folder && s.folderName)} title={entry.name}>{entry.name}</span>
            </span>
            <span className={cx(s.meta, s.size)}>{detail}</span>
            <span className={s.meta}>{relativeTime(entry.modified, now)}</span>
            <span className={s.chevron}>{folder && <Icon name="chevron-right" />}</span>
          </>
        );
        return (
          <li key={entry.name}>
            {folder ? (
              <Link to={filesRoute(projectId, path)} className={s.row}>{cells}</Link>
            ) : (
              <a href={fileUrl(projectId, path)} download={entry.name} className={s.row}>{cells}</a>
            )}
          </li>
        );
      })}
    </ul>
  );
}
