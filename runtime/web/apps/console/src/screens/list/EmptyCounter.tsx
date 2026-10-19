import { Button, Icon } from "@eggie/ui";
import s from "./ProjectList.module.css";

export function EmptyCounter({
  onNew,
  onFolder,
  onGitHub,
  connectedAgent,
}: {
  onNew: () => void;
  onFolder: () => void;
  onGitHub: () => void;
  connectedAgent: string | null;
}) {
  return (
    <div className={s.empty}>
      {connectedAgent && (
        <p className={s.connected} role="status">
          <Icon name="check-circle" />
          {connectedAgent} is connected. Now give it something to work on.
        </p>
      )}
      <div className={s.emptyArt} aria-hidden="true"><Icon name="egg" size="md" /></div>
      <h1 className={s.emptyTitle}>An empty counter</h1>
      <p className={s.emptyText}>Projects live here. Make one and your coding agent finally has somewhere to put things.</p>
      <div className={s.emptyActions}>
        <Button icon="folder" onClick={onFolder}>From a folder</Button>
        <Button icon="branch" onClick={onGitHub}>From GitHub</Button>
        <Button variant="primary" icon="plus" onClick={onNew}>New project</Button>
      </div>
    </div>
  );
}
